@echo off
rem Live health check (Token Factory, vision, Tavily, weather, crop twin) into final_check_log.txt
cd /d "%~dp0.."
set PYTHONUTF8=1
findstr /r /c:"^NEBIUS_API_KEY=..*" .env >NUL 2>&1 || (if exist dist\FieldPilot\.env set FP_ENV_FILE=dist\FieldPilot\.env)
.\.venv\Scripts\python.exe -u -m fieldpilot.cli doctor > final_check_log.txt 2>&1
echo EXIT %errorlevel% >> final_check_log.txt
