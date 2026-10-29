@echo off
rem Bench bridge for the T1: mirrors the console's simulated mission onto the drone's own pump,
rem with the T1 flow law (flow follows ground speed, off below 0.5 m/s).
rem   t1_bench.bat dry     rehearsal, no hardware
rem   t1_bench.bat COM6    the Arduino on COM6 driving the pump (docs/BENCH_RIG.md, Option C)
cd /d "%~dp0.."
if "%1"=="" (set SINK=dry) else if /i "%1"=="dry" (set SINK=dry) else (set SINK=serial:%1)
.\.venv\Scripts\python.exe -m fieldpilot.bench --source console:http://127.0.0.1:8080 --sink %SINK% --flow-law t1_10l
pause
