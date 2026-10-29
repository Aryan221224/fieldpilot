# Feedback on Nebius and NVIDIA tools

Specific, reproducible notes from building FieldPilot. Figures come from the live runs of
29 Oct 2026 (`runs/doctor_*.json`, `runs/day_*.json`, `eval/results/grounding_2026-10-29.json`).

## Nebius Token Factory

- Model used for the agent: `nvidia/nemotron-3-super-120b-a12b` (picked from `GET /v1/models`; 30 models in the catalog, 5 NVIDIA)
- Native tool calling: worked natively. Every step of the live field-agent run used native `tools=`; the JSON fallback was never needed
- Typical latency of one agent step (doctor `nemotron tool calling`): 1.1–1.4 s; one Nemotron-alone answer in the grounding evaluation took 4–10 s
- Reasoning output: Nemotron 3.5 Lightning and 3.5 Super VL wrote a plain-text thinking preamble ("Here's a thinking process: …") straight into `content`, with no `<think>` tags, and ran out of `max_tokens` before the JSON. Our first live run failed on it (the vision check found no JSON in 600 tokens, and booking intake returned nothing). We now send `chat_template_kwargs: {"enable_thinking": false}` where we want a direct answer, and retry once with more room; after that the photo read parsed in 5.1 s and 6 of 7 Hindi bookings parsed
- What was hard: the reasoning switch differs per model and is not in the Token Factory docs, so a JSON caller has to discover it by failing. The agent also once wrote JSON `null` as the string `"None"` for `mission_id`; our tool rejected it and the model corrected itself on the third call
- One change that would have saved us time: a per-model note in the catalog saying whether it reasons by default, where the reasoning goes (`reasoning_content` or `content`) and how to switch it off

## Findings we can already state (verified while building)

- **NVIDIA vision arrived mid-hackathon, and nothing announced it in the hackathon resources.**
  Until October the catalog's NVIDIA models were text-only, so a Physical AI entry wanting
  NVIDIA perception had to stand up its own endpoint. `nvidia/Nemotron-3.5-Super-VL` is now
  public, but entrants who checked earlier would not know. Ask: a "what changed in the
  catalog" feed or changelog for participants.
- **No Nemotron base model for fine-tuning.** The post-training catalog lists Qwen, Llama,
  Gemma, DeepSeek and GPT-OSS, but no Nemotron. We keep operator corrections as a replay test
  set instead. Ask: LoRA on Nemotron 3.5 Lightning, which is exactly the size for
  domain-specific extraction.
- **`response_format: json_schema` works, but the docs example has a syntax error** (a missing
  comma after `"json_schema"` in the Python snippet), and they don't say whether `strict` is
  honoured.
- **Model IDs are not stable in format** (`nemotron-3-super-120b-a12b` vs `Nemotron-3-Ultra-550b-a55b`
  vs `Nemotron-3_5-Lightning`). We pick models by pattern from `GET /v1/models` at start-up.
  Ask: consistent casing and an alias like `nvidia/nemotron-super-latest`.

## Vision model (the catalog VLM used for perception)

- Model: `nvidia/Nemotron-3.5-Super-VL`
- Latency per photo: 5.1 s (doctor, one 1024 px JPEG); images per request: we send up to 3 per zone
- JSON reliability on field photos: with reasoning on, 0 of 1 in the first doctor run (no JSON within 600 tokens); with reasoning off, every zone of the live field-agent run (6 of 6) came back as parseable JSON

## Nemotron on Hindi / Hinglish bookings (operator desk)

- Bookings parsed correctly out of the demo set (`fieldpilot day`): India 6 of 7 (Hindi, Hinglish, a voice-note transcript); Brazil 5 of 5 (Portuguese). The miss was "ढाई बीघा" (2.5 bigha), which the model left empty; code now reads an unambiguous number + unit written in the message and flags it for the call
- Areas the model stated that were not in the message (caught by the check): none in these runs; the check stays, because a wrong area sizes the whole day
- Hindi brief accepted vs rejected by the number check: 1 of 1 accepted on the live India day

## Nemotron models in general

- Where Nemotron was strong in this project: native tool use in the agent loop, reading bookings in Hindi, Hinglish and Portuguese, and briefs in the operator's language
- Where it failed and the policy layer had to catch it (from the eval and run logs): asked alone, Nemotron 3 Super would have flown the drone in 7 of 18 situations where the crop twin says the spray loses money, and in 4 where the zone was unsafe or unverified; FieldPilot flew in none of them

## Tavily

- Verified live on 27 Oct 2026: `include_domains` on ppqs.gov.in returned the CIB&RC fungicide
  PDF and `/extract` gave a row the parser could read (Mancozeb 75 % WP · potato · late blight ·
  1000 g/ha · 500 L · 3-day waiting period) in 2 calls, 3.5–8.9 s. Being able to restrict to
  government domains is what makes the label check possible.
- Gaps: we only measured English queries against Indian government domains; regional-language advisories and state agriculture-university pages were not tested
