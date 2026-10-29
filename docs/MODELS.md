# Which model does which job, and why

FieldPilot uses three NVIDIA Nemotron models on Nebius Token Factory. Each is matched to the
kind of work it gets. None of them is ever the source of a number that reaches a decision.

| Job | Model on Token Factory | Why this one | List price (in / out, per 1M tokens) |
|---|---|---|---|
| **Field agent**: sequences the tools (scan → crop twin → registry → rules → mission), cites advisories, writes the farm proposal in English and Hindi | **Nemotron 3 Super** `nvidia/nemotron-3-super-120b-a12b` (120B, 12B active) | multi-step tool calling with reasoning, where a wrong next step costs a sortie | $0.30 / $0.90 |
| **Perception**: per-zone findings from field photos, cross-checked against pixel measurements, then gated by the conformal set | **Nemotron 3.5 Super VL** `nvidia/Nemotron-3.5-Super-VL` (120B, 12B active) | NVIDIA's vision model on Token Factory since Oct 2026, so the whole pipeline runs on NVIDIA models | $0.30 / $0.90 |
| **Reading bookings**: any language and unit → a job | **Nemotron 3.5 Lightning** `nvidia/Nemotron-3_5-Lightning` (30B, 3B active) | high volume, short, structured; a fifth of Super's price and faster | $0.06 / $0.24 |
| **The crew's brief**, in the operator's language | **Nemotron 3.5 Lightning** | same | $0.06 / $0.24 |

## How each call is used

- **Structured output.** Bookings are read with `response_format: json_schema` and a strict
  schema (`fieldpilot/intake.py: BOOKING_SCHEMA`), so the model returns exactly the fields the
  planner needs. If an endpoint refuses the schema, the client falls back to a prompted JSON
  answer and records which protocol it used. Either way, an area is accepted only if it is
  written in the farmer's message.
- **Native tool calling.** The agent uses `tools=` with a JSON-action fallback
  ([ARCHITECTURE.md](ARCHITECTURE.md)).
- **Number safety.** The brief is rejected if it contains a number that is not in the plan, or
  is not in the operator's script. A registry row from the model counts only if every number
  appears verbatim in the page Tavily returned.
- **Discovery, not hard-coding.** The client reads `/v1/models` and picks each role by pattern
  (`fieldpilot/llm.py`: `pick_agent_model`, `pick_vision_model`, `pick_fast_model`). The models
  actually used are written into every run file. `FP_AGENT_MODEL`, `FP_VISION_MODEL` and
  `FP_FAST_MODEL` pin them.
- **Cost on screen.** Every call goes into a ledger (purpose, model, protocol, tokens). The desk
  shows the day's calls, how many were schema-constrained, the tokens, and the estimated cost at
  each model's list price. Billing → Usage in the console is the authority.

## What we chose not to use, and why

| Option | Why not (today) |
|---|---|
| Nemotron 3 Ultra (550B) for the agent | about 3× Super's price for a tool loop whose decisions are made by code; worth testing only if Super's tool choice proves the bottleneck |
| Nemotron 3 Nano Omni for perception | dedicated-endpoint only on Token Factory; Super VL is public |
| A non-NVIDIA VLM | only as a fallback if the catalog has no NVIDIA VLM; the console then labels it "non-NVIDIA" |
| Fine-tuning Nemotron on Token Factory | Token Factory's fine-tuning catalogue has no Nemotron base model. Operator corrections are kept as a replay test set instead (`cli flywheel`) |

## When operators correct the model

When the operator fixes a field area the model read, the booking and the corrected value are
saved to `runs/flywheel/intake_cases.jsonl`. `python -m fieldpilot.cli flywheel --eval`
replays them through the live model, so a model or prompt change is measured on real
bookings, not on examples we wrote ourselves.

## Offline: next

Nemotron 3.5 Lightning is built to run locally on Jetson systems and RTX GPUs. Pointing
`NEBIUS_BASE_URL` at a local OpenAI-compatible server is all the client needs. A Jetson box in
the operator's vehicle is the planned route to villages without coverage. It is **not built
or tested yet** ([DEPLOYMENT.md](DEPLOYMENT.md#7-next-offline-in-the-vehicle)).

## Sources

- Token Factory Nemotron catalog and prices: https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference
- Token Factory structured output: https://docs.tokenfactory.nebius.com/ai-models-inference/json.md
- Token Factory fine-tuning models: https://docs.tokenfactory.nebius.com/post-training/models.md
- Nemotron 3.5 Lightning: https://interestingengineering.com/ai-robotics/nvidia-nemotron-35-lightning-autonomous-agents
- Nemotron 3.5 Super VL: https://docs.nvidia.com/nemo/megatron-bridge/nightly/models/nemotron/nemotron3.5-super-vl.html
