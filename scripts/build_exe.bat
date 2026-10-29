@echo off
rem Builds dist\FieldPilot\FieldPilot.exe (a desktop app: own window, no console, no terminal).
rem Takes a few minutes. Log: build_exe_log.txt
cd /d "%~dp0.."
set PY=.\.venv\Scripts\python.exe
echo ===== build %date% %time% ===== > build_exe_log.txt
taskkill /F /IM FieldPilot.exe >> build_exe_log.txt 2>&1
ping -n 4 127.0.0.1 >nul
tasklist | findstr /i "FieldPilot webview2 python" >> build_exe_log.txt
if exist dist\FieldPilot\.env copy /Y dist\FieldPilot\.env "%TEMP%\fp_env_keep" >nul
if exist dist\FieldPilot rmdir /s /q dist\FieldPilot >> build_exe_log.txt 2>&1
if exist dist\FieldPilot (echo LOCKED: dist\FieldPilot could not be removed >> build_exe_log.txt & exit /b 1)
%PY% -m pip install pyinstaller pywebview >> build_exe_log.txt 2>&1
%PY% -m PyInstaller --noconfirm --clean --windowed --name FieldPilot --paths . --collect-all pcse --collect-all pcse_gui --collect-submodules fieldpilot --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto --hidden-import uvicorn.lifespan.on --hidden-import webview.platforms.edgechromium --hidden-import statistics --hidden-import matplotlib.pyplot --hidden-import matplotlib.backends.backend_agg scripts\fieldpilot_launcher.py >> build_exe_log.txt 2>&1
if not exist dist\FieldPilot\FieldPilot.exe (echo BUILD FAILED >> build_exe_log.txt & exit /b 1)
for %%D in (web vendor examples sim docs eval) do xcopy /E /I /Y /Q %%D dist\FieldPilot\%%D >> build_exe_log.txt 2>&1
if not exist dist\FieldPilot\runs mkdir dist\FieldPilot\runs
for %%D in (fieldday sitl) do if exist runs\%%D robocopy runs\%%D dist\FieldPilot\runs\%%D /E /XF *.BIN *.bin *.tlog >> build_exe_log.txt 2>&1
if exist "%TEMP%\fp_env_keep" (copy /Y "%TEMP%\fp_env_keep" dist\FieldPilot\.env >> build_exe_log.txt) else if exist .env (copy /Y .env dist\FieldPilot\.env >> build_exe_log.txt) else (copy /Y .env.example dist\FieldPilot\.env >> build_exe_log.txt)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\FieldPilot.lnk'); $s.TargetPath=(Resolve-Path dist\FieldPilot\FieldPilot.exe).Path; $s.WorkingDirectory=(Resolve-Path dist\FieldPilot).Path; $s.Save()" >> build_exe_log.txt 2>&1
echo BUILD OK: dist\FieldPilot\FieldPilot.exe >> build_exe_log.txt
