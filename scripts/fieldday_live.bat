@echo off
rem Simulated field day on the latest code: live Nemotron reads the bookings and writes the brief,
rem live forecast; every sortie flown by the K3-A Pro twin. Log: fieldday_live_log.txt
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
findstr /r /c:"^NEBIUS_API_KEY=..*" .env >NUL 2>&1 || (if exist dist\FieldPilot\.env set FP_ENV_FILE=dist\FieldPilot\.env)
.\.venv\Scripts\python.exe -u -m fieldpilot.cli fieldday --live --date 2026-10-31 > fieldday_live_log.txt 2>&1
echo EXIT %errorlevel% >> fieldday_live_log.txt
