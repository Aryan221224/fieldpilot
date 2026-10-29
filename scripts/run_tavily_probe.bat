@echo off
cd /d "%~dp0.."
.\.venv\Scripts\python.exe scripts\tavily_probe.py > tavily_probe_stdout.txt 2>&1
