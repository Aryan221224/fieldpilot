@echo off
rem FieldPilot console for the T1 bench clip: simulator in real time (FP_SIM_SPEED=1).
cd /d "%~dp0.."
set FP_DRONE=sim
set FP_SIM_SPEED=1
echo Console: http://127.0.0.1:8080   (Ctrl-C to stop)
.\.venv\Scripts\python.exe -m uvicorn fieldpilot.api:app --port 8080
