@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Download: https://www.python.org/downloads/
  pause
  exit /b 1
)
python app.py
if errorlevel 1 pause
