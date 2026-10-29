# Final checklist — deadline Fri 30 Oct 2026, 10:00 PT (22:30 IST). Submit by Wed 28 Oct.

## Tue 27 Oct

- [x] SOP gate, label check, paperwork and bench merged; console fixed; 112 tests pass
- [ ] `.env` with `NEBIUS_API_KEY` and `TAVILY_API_KEY`, then `python -m fieldpilot.cli models`
- [ ] `python -m fieldpilot.cli doctor` shows no FAIL (a live Token Factory call is the Stage 1 pass/fail)
- [ ] One live run in the console, `runs/R-*.json` kept for the README and video
- [ ] `python eval/grounding_eval.py` then `python eval/make_charts.py --fill-submission`
- [x] Project runs on our T1 10 L quad (JIYI K3-A Pro): `python -m fieldpilot.cli airframe`; 188 tests pass (1 more flies ArduCopter SITL when `FP_ARDUPILOT` is set)
- [ ] T1 bench (docs/BENCH_RIG.md, Option C): battery out, props folded, pump lead to the Arduino + IRLZ44N + 24 V supply; flash `firmware/pump_bridge` (FLOW mode)
- [ ] Rehearse: `scripts\t1_console.bat` + `scripts\t1_bench.bat dry`, Run demo → Approve
- [ ] `python -m fieldpilot.bench --check --sink serial:COM6`, then film one take of at least 75 s with `scripts\t1_bench.bat COM6`
- [ ] Cut ~10 s of the team's field clip (manual flight) for the video opening; label it "manual flight"

## Wed 28 Oct

- [ ] Record the console in OBS, edit to under 3:00 following docs/VIDEO_SCRIPT.md, with voice-over
- [ ] Upload to YouTube as **Public**; check that it plays logged out
- [ ] Push to a **public** GitHub repo; check the Apache-2.0 licence shows in the About sidebar
- [ ] `python -m fieldpilot.cli doctor --submission` shows no FAIL
- [ ] Fill the blanks in docs/FEEDBACK.md and docs/TAVILY.md
- [ ] Devpost: paste the fields from docs/SUBMISSION.md, add the repo and video links, choose track = Physical AI, paste the feedback, then **Submit**

## Thu 29 – Fri 30 Oct

- [ ] Fixes only. Do not start new features.
