@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Download: https://www.python.org/downloads/
  pause
  exit /b 1
)
rem Give Python its own console so Ctrl+C does not trigger cmd.exe's batch prompt.
start "Fujian CSP Simulator" python "%~dp0app.py"
exit /b
