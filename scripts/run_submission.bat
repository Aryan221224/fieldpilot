@echo off
rem Every real run the Devpost submission needs, in order. Log: submission_log.txt
rem Uses the key saved in the app (dist\FieldPilot\.env) if the project .env has none.
cd /d "%~dp0.."
set PY=.\.venv\Scripts\python.exe
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
findstr /r /c:"^NEBIUS_API_KEY=..*" .env >nul 2>&1 || (if exist dist\FieldPilot\.env set FP_ENV_FILE=dist\FieldPilot\.env)
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).AddDays(1).ToString('yyyy-MM-dd')"') do set TOMORROW=%%d
echo ===== submission run %date% %time% (plan date %TOMORROW%) ===== > submission_log.txt
call :step "tests" -m pytest -q -p no:cacheprovider
call :step "models" -m fieldpilot.cli models
call :step "doctor (live)" -m fieldpilot.cli doctor
call :step "field agent demo, live Nemotron" -m fieldpilot.cli demo --speed 30
call :step "operator day, India" -m fieldpilot.cli day examples\desk_demo_in.json --date %TOMORROW% --fill-submission
call :step "operator day, Brazil" -m fieldpilot.cli day examples\desk_demo_br.json --date %TOMORROW%
call :step "benchmark, five countries" eval\efficiency_bench.py --fill-submission
call :step "grounding evaluation, Nemotron alone" eval\grounding_eval.py
call :step "charts" eval\make_charts.py --fill-submission
call :step "doctor --submission" -m fieldpilot.cli doctor --submission
echo. >> submission_log.txt
echo ===== ALL DONE %date% %time% ===== >> submission_log.txt
exit /b 0

:step
echo. >> submission_log.txt
echo ===== %~1 [%time%] ===== >> submission_log.txt
%PY% %2 %3 %4 %5 %6 %7 %8 %9 >> submission_log.txt 2>&1
echo ----- %~1: exit %errorlevel% >> submission_log.txt
exit /b 0
