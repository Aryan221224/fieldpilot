# Nebius setup

Everything FieldPilot needs from Nebius, in order. Budget: the free $50 in Token Factory
credits covers development, the desk and the eval.

## 1. Credits and key

1. Register on Devpost for the hackathon, then claim **$25 Token Factory credits** with the form
   on the hackathon Resources page (activation code `NEBIUS-DEVPOST-GLOBAL26`).
2. Join the **Nebius Builders Program** (dev.nebius.com/builders) for another $25 plus Tavily credits.
3. Create an API key in the Token Factory console.
4. `cp .env.example .env` and set `NEBIUS_API_KEY=...`

## 2. Check what your catalog offers

```
python3 -m fieldpilot.cli models
```

Output lists every model (NVIDIA ones starred) and the two picks. Expected: the agent is a
Nemotron (Super preferred), the vision model Nemotron 3.5 Super VL. If the vision line says `None`, do step 3.

To pin models instead of auto-picking, set `FP_AGENT_MODEL` / `FP_VISION_MODEL` in `.env`.

## 3. Perception

Perception uses NVIDIA Nemotron 3.5 Super VL from the catalog. If a catalog has no NVIDIA VLM,
it falls back to the best other one and the console labels it "non-NVIDIA". To use another
OpenAI-compatible vision endpoint (for example a Cosmos-Reason model you serve yourself), set
`FP_VISION_BASE_URL`, `FP_VISION_API_KEY` and `FP_VISION_MODEL` in `.env`. Bookings and the
brief go to Nemotron 3.5 Lightning; pin another model with `FP_FAST_MODEL`. Which model does
what, and why: [MODELS.md](MODELS.md).

## 4. What calls Token Factory at runtime

| Call | Where |
|---|---|
| Booking intake (Hindi/Hinglish → job JSON) | `fieldpilot/intake.py` |
| Operator's Hindi morning brief | `fieldpilot/desk.py` |
| Field agent tool loop | `fieldpilot/agent.py`, `llm.py` |
| Label row, when the parser cannot read it | `fieldpilot/label.py` |
| Grounding eval (Nemotron alone vs FieldPilot) | `eval/grounding_eval.py` |
