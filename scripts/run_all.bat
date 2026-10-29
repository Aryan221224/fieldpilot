@echo off
rem FieldPilot: everything that runs on this laptop without a Token Factory key, in order.
rem Output: run_all_log.txt (and the files each step names). Then the console opens on /ops.
cd /d "%~dp0.."
set PY=.\.venv\Scripts\python.exe
echo ===== FieldPilot run_all %date% %time% ===== > run_all_log.txt
echo ===== 1. tests ===== >> run_all_log.txt
%PY% -m pytest -q -p no:warnings >> run_all_log.txt 2>&1
echo ===== 2. the T1 airframe ===== >> run_all_log.txt
%PY% -m fieldpilot.cli airframe >> run_all_log.txt 2>&1
echo ===== 3. efficiency benchmark on live forecasts (fills [EFF]) ===== >> run_all_log.txt
%PY% eval\efficiency_bench.py --fill-submission >> run_all_log.txt 2>&1
echo ===== 4. simulated field day on tomorrow's live forecast ===== >> run_all_log.txt
%PY% -m fieldpilot.cli fieldday >> run_all_log.txt 2>&1
echo ===== 5. rehearsal (stand-in Token Factory, emulated pump, both operator days) ===== >> run_all_log.txt
%PY% -m fieldpilot.cli rehearse >> run_all_log.txt 2>&1
echo ===== DONE %time% ===== >> run_all_log.txt
echo Console: http://127.0.0.1:8080/ops   (Ctrl-C to stop)
start "" http://127.0.0.1:8080/ops
%PY% -m uvicorn fieldpilot.api:app --port 8080
