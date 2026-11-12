@echo off
cd /d "%~dp0"
set BT_HOST=0.0.0.0
set BT_OPEN=1
python server.py
if errorlevel 1 (
  echo.
  echo [!] Startup failed. Please make sure Python 3.9+ is installed.
  echo     Run "python server.py" in this folder to see the error.
  echo.
  pause
)