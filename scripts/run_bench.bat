@echo off
cd /d "%~dp0.."
echo ===== T1 check %date% %time% ===== > bench_log.txt
.\.venv\Scripts\python.exe -m pytest -q -p no:warnings >> bench_log.txt 2>&1
echo ===== airframe ===== >> bench_log.txt
.\.venv\Scripts\python.exe -m fieldpilot.cli airframe >> bench_log.txt 2>&1
echo ===== efficiency benchmark ===== >> bench_log.txt
.\.venv\Scripts\python.exe eval\efficiency_bench.py --fill-submission >> bench_log.txt 2>&1
echo ===== DONE ===== >> bench_log.txt
