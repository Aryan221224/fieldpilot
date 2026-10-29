# FieldPilot as a Windows app (FieldPilot.exe)

FieldPilot runs as a normal desktop app: double-click `FieldPilot.exe`, no terminal and no
command to start a server. It opens its own window on the operator desk (it uses the Edge
WebView2 runtime that ships with Windows 11; without it, the default browser opens instead).

## Build it

1. Double-click `scripts\build_exe.bat` (about 5 minutes; log in `build_exe_log.txt`).
2. The app is `dist\FieldPilot\FieldPilot.exe`, and a Desktop shortcut is made.
3. Keep the whole `dist\FieldPilot` folder together. `web`, `vendor`, `examples`, `sim`,
   `eval`, `docs`, `runs` and `.env` sit beside the exe and it reads them from there.
4. Rebuilding closes a running FieldPilot, clears the old build and keeps the `.env` you saved
   in `dist\FieldPilot`. Do not keep File Explorer open inside `dist\FieldPilot` while it builds.

## Keys

Open **settings** in the app, paste the Nebius Token Factory key (and the Tavily key), press
Save, then **Test the key**. The test makes one tiny call to the fast Nemotron and shows the
three models it picked. The keys are written to the `.env` beside the exe and shown masked.
Settings only accept requests from this computer.

## Simulation lab (`/sims`)

Every simulation runs from one page, one at a time, with its console output and report:

| Simulation | Notes |
|---|---|
| Simulated field day | the whole operator day on the production planner; results open in field ops |
| Rehearsal | whole pipeline against a mock Token Factory; predicts the real run |
| K3-A Pro twin | flies a flight card through the controller's behavioural twin |
| T1 pump and nozzles | bench emulator, litres with the uncertainty range |
| Benchmark | FieldPilot against the common way, five countries (needs the internet) |
| Grounding evaluation | crop twin and label parser; tick off "No LLM" to run Nemotron alone as the baseline |
| Airframe | T1 hover, flow law, take-off category, sorties per day |
| FieldMast simulator | posts simulated mast readings to this app |
| Deviation | newest rehearsal against whatever real results exist |
| ArduCopter firmware (SITL) | Linux/WSL only; saved runs open in field ops → Firmware |

Everything is labelled SIMULATED or REHEARSAL and none of it can fill `docs/SUBMISSION.md`. A run
with synthetic weather (Open-Meteo unreachable) is marked as such and is not for the submission:
run it again when the weather service answers.
