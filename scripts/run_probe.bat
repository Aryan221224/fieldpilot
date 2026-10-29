@echo off
cd /d "%~dp0.."
.\.venv\Scripts\python.exe scripts\udp_probe.py > probe_log.txt 2>&1
echo DONE >> probe_log.txt
