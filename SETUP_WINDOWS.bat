@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto fail
echo Setup complete. Run START_WINDOWS.bat
pause
exit /b 0
:fail
echo Setup failed. Check Python 3.10+ and your internet connection.
pause
exit /b 1
