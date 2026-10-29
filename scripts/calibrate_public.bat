@echo off
rem Calibrate the perception gate on 150 public, labelled potato leaf photos. Log: calibrate_log.txt
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
findstr /r /c:"^NEBIUS_API_KEY=..*" .env >NUL 2>&1 || (if exist dist\FieldPilot\.env set FP_ENV_FILE=dist\FieldPilot\.env)
.\.venv\Scripts\python.exe -u eval\public_scans.py all > calibrate_log.txt 2>&1
echo EXIT %errorlevel% >> calibrate_log.txt
