# Developing on the lab PC's GPU (RTX A6000, 48 GB)

Every live FieldPilot run on Nebius spends credits. On the lab PC the same
Nemotron models run locally for free, which is the right place to iterate on
prompts, the tool loop and the console. The hackathon entry itself still runs
on Nebius.

## What runs where

| Work | Where | Why |
|---|---|---|
| Prompt and tool-loop iteration, console work, SITL rehearsals | **lab PC GPU** (Ollama) | free, unlimited |
| Every run quoted in the video, the README or Devpost | **Nebius Token Factory** | the rules require it |
| The grounding evaluation (`eval/grounding_eval.py`) | **Nebius** (Token Factory) | the number is a claim about Nemotron on Nebius |
| Calibrating the perception gate (`cli calibrate`) | **the model that will be used live** | a gate calibrated on a different model does not transfer |

Local runs are labelled `LOCAL-GPU (not Nebius)` in the console badge, every run
file and the doctor, and `doctor --submission` ignores them.

## Setup (once)

1. Install Ollama for Windows from https://ollama.com/download (per-user install, no admin needed).
2. `ollama pull nemotron-3-nano:30b` (about 24 GB; the agent)
3. `ollama pull nemotron3:33b` (about 28 GB; Nemotron 3 with image input, for perception)
4. `cd C:\path\to\fieldpilot`
5. `copy .env.local.example .env.local`

Both models fit in the A6000's 48 GB one at a time; Ollama swaps them as needed.

## Use

1. `$env:FP_ENV_FILE=".env.local"`
2. `..\.venv-fp\Scripts\python.exe -m fieldpilot.cli doctor` — the `nebius` row should say LOCAL (expected)
3. `..\.venv-fp\Scripts\python.exe -m fieldpilot.cli run photos\`
4. `Remove-Item Env:FP_ENV_FILE` to go back to Nebius (`.env`)

If Nemotron in Ollama does not emit native tool calls, FieldPilot switches to its
JSON tool protocol automatically (the run's trace shows `protocol: json`).
