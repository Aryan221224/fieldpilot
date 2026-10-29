"""Nebius Token Factory client: model discovery, tool calling, vision.

Why discovery instead of hard-coded model IDs
---------------------------------------------
Token Factory's catalog changes (models are added, renamed and retired), and
the published lists disagree with each other. A hard-coded ID that 404s on
demo day is the most avoidable failure in the whole project. So the client asks
``GET /v1/models`` once, and picks by preference *pattern*:

* agent brain  -> an NVIDIA Nemotron text model (Super > Lightning > Ultra > any)
* perception   -> Nemotron Omni / VL, then Cosmos-Reason, then any VLM

The pick is logged into every trace, so the video and the README can say
exactly which model produced which step. ``FP_AGENT_MODEL`` / ``FP_VISION_MODEL``
override it.

Why a JSON fallback for tool calls
----------------------------------
Native function calling depends on the serving stack's tool parser for that
model. If the endpoint rejects ``tools=`` or the model answers in prose, the
client falls back to a documented JSON-action protocol
(``{"tool": ..., "arguments": {...}}``) so the agent degrades instead of dying.
Which path was used is recorded per step.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import Config

log = logging.getLogger(__name__)

AGENT_PREFERENCE = [
    "nemotron-3-super", "nemotron-3_5-lightning", "nemotron-3.5-lightning",
    "nemotron-3-ultra", "nemotron-3-nano", "llama-3_3-nemotron-super",
    "nemotron-super", "nemotron",
]
VISION_PREFERENCE = [
    ("nemotron", "super", "vl"), ("nemotron", "vl"), ("cosmos-reason",), ("nemotron", "omni"),
    ("qwen3-vl",), ("qwen2.5-vl",), ("minicpm-v",), ("qwen3.8",), ("kimi-k",),
    ("glm-5",), ("gemma-3",), ("llama-4",), ("pixtral",), ("minimax-m",),
]
# Perception: NVIDIA Nemotron 3.5 Super VL (nvidia/Nemotron-3.5-Super-VL, 120B / 12B active,
# public on Token Factory since Oct 2026) is preferred. Nano Omni is dedicated-only there, so
# it ranks after it. Only if the catalog has no NVIDIA VLM does perception fall back to
# another VLM, and the console/trace label it "non-NVIDIA" (is_nvidia).

# High-volume language work at the edges of the day (reading bookings, writing the brief)
# goes to the fast, cheap Nemotron: 3.5 Lightning (30B / 3B active, trained for agent tool
# use). The agent's multi-step tool loop stays on Nemotron 3 Super. FP_FAST_MODEL overrides.
FAST_PREFERENCE = ["nemotron-3_5-lightning", "nemotron-3.5-lightning", "nemotron-3-nano"]
FAST_PURPOSES = ("intake", "brief")

# Token Factory list prices, USD per 1M tokens (input, output), Oct 2026 - for the per-day
# estimate only; Billing -> Usage is the authority. Matched by substring, first hit wins.
PRICES = [("lightning", 0.06, 0.24), ("nano", 0.06, 0.24), ("ultra", 1.00, 3.00),
          ("super", 0.30, 0.90)]
DEFAULT_PRICE = (0.30, 0.90)


def price_of(model: str | None) -> tuple[float, float]:
    m = (model or "").lower()
    for key, pin, pout in PRICES:
        if key in m:
            return pin, pout
    return DEFAULT_PRICE


def pick_fast_model(ids: list[str]) -> str | None:
    low = {i.lower(): i for i in ids}
    for pat in FAST_PREFERENCE:
        for k, orig in low.items():
            if pat in k and not any(v in k for v in ("omni", "-vl", "embed")):
                return orig
    return None


def pick_agent_model(ids: list[str]) -> str | None:
    low = {i.lower(): i for i in ids}
    for pat in AGENT_PREFERENCE:
        for k, orig in low.items():
            if pat in k and not any(v in k for v in ("omni", "-vl", "embed", "reward", "guard")):
                return orig
    return None


def pick_vision_model(ids: list[str]) -> str | None:
    low = {i.lower(): i for i in ids}
    for pats in VISION_PREFERENCE:
        for k, orig in low.items():
            if all(p in k for p in pats) and "embed" not in k:
                return orig
    return None


def is_nvidia(model_id: str | None) -> bool:
    m = (model_id or "").lower()
    return "nvidia" in m or "nemotron" in m or "cosmos" in m


_THINK = re.compile(r"<think>(.*?)</think>", re.S | re.I)


def split_reasoning(text: str | None) -> tuple[str, str]:
    """Separate a <think> block (Nemotron / Cosmos reasoning) from the answer."""
    if not text:
        return "", ""
    thoughts = " ".join(t.strip() for t in _THINK.findall(text))
    answer = _THINK.sub("", text)
    # Unclosed think block: everything before </think> or the whole thing.
    if "<think>" in answer.lower():
        answer = answer.split("<think>")[0]
    m = re.search(r"<answer>(.*?)</answer>", answer, re.S | re.I)
    if m:
        answer = m.group(1)
    return thoughts.strip(), answer.strip()


def extract_json(text: str) -> Any:
    """First JSON object/array in text, tolerant of fences and reasoning."""
    _, text = split_reasoning(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    raise ValueError("no JSON object found in model output")


def image_to_data_url(img_bytes: bytes, max_side: int = 1024) -> str:
    """Downscale and JPEG-encode. Field photos are 12 MP; the model needs ~1 MP."""
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Step:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    protocol: str = "native"        # native | json | stub
    model: str = ""
    usage: dict = field(default_factory=dict)


JSON_PROTOCOL = (
    "\n\nTOOL PROTOCOL (fallback): if you cannot emit native tool calls, reply with "
    "exactly one JSON object and nothing else: "
    '{"tool": "<tool name>", "arguments": {...}}. '
    "Call one tool per reply. Never invent numbers a tool has not returned."
)


def usage_summary(ledger: list[dict]) -> dict:
    """Calls, tokens and estimated USD (each call at its own model's list price)."""
    pin = sum(int(r.get("prompt") or 0) for r in ledger)
    pout = sum(int(r.get("completion") or 0) for r in ledger)
    usd = 0.0
    by: dict[str, int] = {}
    models: dict[str, int] = {}
    for r in ledger:
        by[r.get("purpose", "?")] = by.get(r.get("purpose", "?"), 0) + 1
        models[r.get("model") or "?"] = models.get(r.get("model") or "?", 0) + 1
        a, b = price_of(r.get("model"))
        usd += int(r.get("prompt") or 0) / 1e6 * a + int(r.get("completion") or 0) / 1e6 * b
    return {"calls": len(ledger), "prompt_tokens": pin, "completion_tokens": pout,
            "usd_estimate": round(usd, 5), "calls_by_purpose": by, "calls_by_model": models,
            "schema_constrained_calls": sum(1 for r in ledger if r.get("protocol") == "json_schema")}


class TokenFactory:
    """Thin OpenAI-compatible client for Nebius Token Factory (and endpoints)."""

    def __init__(self, cfg: Config):
        from openai import OpenAI
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.nebius_base_url, api_key=cfg.nebius_api_key,
                             timeout=120.0, max_retries=2)
        self.vision_client = self.client
        if cfg.vision_base_url:
            vb = cfg.vision_base_url.rstrip("/")
            if not vb.endswith("/v1"):
                vb += "/v1"
            self.vision_client = OpenAI(base_url=vb, api_key=cfg.vision_api_key or "none",
                                        timeout=180.0, max_retries=1)
        self._catalog: list[str] | None = None
        self._native_tools_ok: bool | None = None
        self.agent_model = cfg.agent_model or None
        self.vision_model = cfg.vision_model or None
        self.fast_model = getattr(cfg, "fast_model", "") or None
        self._schema_ok: bool | None = None
        self.ledger: list[dict] = []          # one row per Token Factory call
        self._nothink_ok: bool | None = None

    def _create(self, client, **kw):
        """chat.completions.create with Nemotron's reasoning switched off where a direct answer is
        wanted (JSON, a brief, a photo read). The 3.5 models otherwise write a long plain-text
        thinking preamble first and can run out of tokens before the answer. If the endpoint
        rejects the switch, the call is repeated without it, once, and remembered."""
        if self._nothink_ok is not False:
            try:
                r = client.chat.completions.create(
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}}, **kw)
                self._nothink_ok = True
                return r
            except Exception as e:  # noqa: BLE001
                status = getattr(e, "status_code", None)
                if status not in (400, 422) or self._nothink_ok is True:
                    raise
                log.warning("endpoint refused enable_thinking=false (%s); asking without it", e)
                self._nothink_ok = False
        return client.chat.completions.create(**kw)

    def _log(self, purpose: str, r, protocol: str, model: str | None = None) -> None:
        u = _usage(r)
        self.ledger.append({"purpose": purpose, "model": model or self.agent_model,
                            "protocol": protocol, **u})

    def usage(self) -> dict:
        return usage_summary(self.ledger)

    def model_for(self, purpose: str) -> str:
        """Bookings and the brief go to the fast Nemotron; everything else to the agent model."""
        self.resolve()
        return (self.fast_model or self.agent_model) if purpose in FAST_PURPOSES else self.agent_model

    # -- catalog ------------------------------------------------------------
    def catalog(self) -> list[str]:
        if self._catalog is None:
            try:
                self._catalog = sorted(m.id for m in self.client.models.list().data)
            except Exception as e:  # noqa: BLE001
                log.warning("model catalog unavailable: %s", e)
                self._catalog = []
        return self._catalog

    def resolve(self) -> dict[str, str | None]:
        ids = self.catalog()
        if not self.agent_model:
            self.agent_model = pick_agent_model(ids) or "nvidia/nemotron-3-super-120b-a12b"
        if not self.fast_model:
            self.fast_model = pick_fast_model(ids) or self.agent_model
        if not self.vision_model:
            if self.cfg.vision_base_url:
                try:
                    vids = [m.id for m in self.vision_client.models.list().data]
                    self.vision_model = vids[0] if vids else "nvidia/Cosmos-Reason2-8B"
                except Exception:  # noqa: BLE001
                    self.vision_model = "nvidia/Cosmos-Reason2-8B"
            else:
                self.vision_model = pick_vision_model(ids)
        return {"agent": self.agent_model, "vision": self.vision_model}

    # -- agent step -------------------------------------------------------------
    def step(self, messages: list[dict], tools: list[dict], temperature: float = 0.2,
             max_tokens: int = 2048) -> Step:
        self.resolve()
        if self._native_tools_ok is not False:
            try:
                r = self.client.chat.completions.create(
                    model=self.agent_model, messages=messages, tools=tools,
                    tool_choice="auto", temperature=temperature, max_tokens=max_tokens)
                self._log("agent", r, "native")
                msg = r.choices[0].message
                reasoning_attr = getattr(msg, "reasoning_content", None) or ""
                thoughts, content = split_reasoning(msg.content or "")
                calls = []
                for tc in msg.tool_calls or []:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = extract_json(tc.function.arguments)
                    calls.append(ToolCall(tc.id, tc.function.name, args))
                if calls or not self._looks_like_json_action(content):
                    self._native_tools_ok = True
                    return Step(content=content, reasoning=(reasoning_attr or thoughts)[:4000],
                                tool_calls=calls, protocol="native", model=self.agent_model,
                                usage=_usage(r))
                # Model answered with a JSON action in prose: honour it.
                return self._parse_json_action(content, thoughts, r)
            except Exception as e:  # noqa: BLE001
                if self._native_tools_ok is True:
                    raise
                log.warning("native tool calling unavailable (%s); using JSON protocol", e)
                self._native_tools_ok = False

        sys_extra = JSON_PROTOCOL + "\nAvailable tools:\n" + json.dumps(
            [t["function"] for t in tools], indent=1)
        msgs = _flatten_for_json_protocol(messages, sys_extra)
        r = self.client.chat.completions.create(
            model=self.agent_model, messages=msgs, temperature=temperature, max_tokens=max_tokens)
        self._log("agent", r, "json")
        thoughts, content = split_reasoning(r.choices[0].message.content or "")
        return self._parse_json_action(content, thoughts, r)

    @staticmethod
    def _looks_like_json_action(text: str) -> bool:
        return '"tool"' in text and "{" in text

    def _parse_json_action(self, content: str, thoughts: str, r) -> Step:
        try:
            obj = extract_json(content)
        except ValueError:
            return Step(content=content, reasoning=thoughts[:4000], protocol="json",
                        model=self.agent_model, usage=_usage(r))
        if isinstance(obj, dict) and obj.get("tool"):
            return Step(content="", reasoning=thoughts[:4000], protocol="json",
                        model=self.agent_model, usage=_usage(r),
                        tool_calls=[ToolCall("call_" + uuid.uuid4().hex[:8], obj["tool"],
                                             obj.get("arguments") or {})])
        return Step(content=content, reasoning=thoughts[:4000], protocol="json",
                    model=self.agent_model, usage=_usage(r))

    # -- vision -------------------------------------------------------------------
    def vision_json(self, prompt: str, images: list[bytes], system: str = "",
                    max_tokens: int = 1800) -> tuple[Any, dict]:
        self.resolve()
        if not self.vision_model:
            raise RuntimeError("no vision-capable model in the Token Factory catalog; "
                               "deploy Cosmos-Reason2 (nebius/deploy_perception_endpoint.sh)")
        # Self-hosted vLLM endpoints often cap images per prompt at 1 by default.
        images = images[:1] if self.cfg.vision_base_url else images[:3]

        def ask(imgs):
            content: list[dict] = [{"type": "text", "text": prompt}]
            for b in imgs:
                content.append({"type": "image_url", "image_url": {"url": image_to_data_url(b)}})
            msgs = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": content}]
            return self._create(self.vision_client, model=self.vision_model, messages=msgs,
                                temperature=0.1, max_tokens=max(max_tokens, 3000))

        try:
            r = ask(images)
        except Exception as e:  # noqa: BLE001
            if len(images) > 1 and "400" in str(e):
                log.warning("vision endpoint rejected %d images; retrying with 1", len(images))
                r = ask(images[:1])
            else:
                raise
        self._log("vision", r, "prompt", self.vision_model)
        msg = r.choices[0].message
        text = msg.content or ""
        try:
            extract_json(text)
        except ValueError:
            # No JSON yet: most often a reasoning preamble that ran out of tokens. One more try
            # with twice the room before giving up.
            r = self._create(self.vision_client, model=self.vision_model,
                             messages=([{"role": "system", "content": system}] if system else []) +
                             [{"role": "user", "content": [{"type": "text", "text": prompt}] +
                               [{"type": "image_url", "image_url": {"url": image_to_data_url(b)}}
                                for b in images[:1]]}],
                             temperature=0.1, max_tokens=max(max_tokens, 3000) * 2)
            self._log("vision", r, "prompt", self.vision_model)
            msg = r.choices[0].message
            text = msg.content or ""
        thoughts, _ = split_reasoning(text)
        thoughts = thoughts or (getattr(msg, "reasoning_content", None) or "")
        return extract_json(text), {"model": self.vision_model, "reasoning": thoughts[:3000],
                                    "usage": _usage(r)}

    def text(self, messages: list[dict], max_tokens: int = 1200, temperature: float = 0.3,
             purpose: str = "text") -> str:
        self.resolve()
        model = self.model_for(purpose)
        r = self._create(self.client, model=model, messages=messages, temperature=temperature,
                         max_tokens=max_tokens)
        self._log(purpose, r, "prompt", model)
        return split_reasoning(r.choices[0].message.content or "")[1]

    def json(self, messages: list[dict], schema: dict, name: str, max_tokens: int = 800,
             purpose: str = "json") -> tuple[Any, str]:
        """JSON constrained by ``schema`` (Token Factory ``response_format: json_schema``).

        Falls back to a prompted answer parsed with ``extract_json`` if the endpoint or model
        refuses the schema. Returns (object, protocol) with protocol ``json_schema`` or
        ``prompt``. Either way the caller still checks every number against its source.
        """
        self.resolve()
        if self._schema_ok is not False:
            try:
                model = self.model_for(purpose)
                r = self._create(
                    self.client, model=model, messages=messages, temperature=0.0,
                    max_tokens=max_tokens,
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": name, "schema": schema,
                                                     "strict": True}})
                self._log(purpose, r, "json_schema", model)
                try:
                    obj = extract_json(r.choices[0].message.content or "")
                except ValueError:
                    # The schema call worked but the answer ran out of tokens (a reasoning
                    # preamble): ask again with room to finish, still schema-constrained.
                    r = self._create(
                        self.client, model=model, messages=messages, temperature=0.0,
                        max_tokens=max_tokens * 4,
                        response_format={"type": "json_schema",
                                         "json_schema": {"name": name, "schema": schema,
                                                         "strict": True}})
                    self._log(purpose, r, "json_schema", model)
                    obj = extract_json(r.choices[0].message.content or "")
                self._schema_ok = True
                return obj, "json_schema"
            except Exception as e:  # noqa: BLE001
                if self._schema_ok is True:
                    log.warning("schema-constrained call failed once (%s); prompting instead", e)
                else:
                    log.warning("response_format json_schema unavailable (%s); prompting", e)
                    self._schema_ok = False
        raw = self.text(messages, max_tokens=max_tokens * 4, temperature=0.0, purpose=purpose)
        return extract_json(raw), "prompt"


def _usage(r) -> dict:
    u = getattr(r, "usage", None)
    if not u:
        return {}
    return {"prompt": getattr(u, "prompt_tokens", 0), "completion": getattr(u, "completion_tokens", 0)}


def _flatten_for_json_protocol(messages: list[dict], sys_extra: str) -> list[dict]:
    """Rewrite tool-role messages as user turns for endpoints without tools."""
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            out.append({"role": "system", "content": m["content"] + sys_extra})
        elif role == "tool":
            out.append({"role": "user", "content": f"TOOL RESULT ({m.get('name', '')}):\n{m['content']}"})
        elif role == "assistant" and m.get("tool_calls"):
            tc = m["tool_calls"][0]["function"]
            out.append({"role": "assistant", "content": json.dumps(
                {"tool": tc["name"], "arguments": json.loads(tc["arguments"] or "{}")})})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out
