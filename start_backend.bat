@echo off
rem Starts the TrueCost backend on http://localhost:8010 (double-click this file).
rem Leave this window open while you demo. Close it (or press Ctrl+C) to stop the backend.
setlocal
cd /d "%~dp0backend"

set "PY=C:\Users\biswa\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

echo Stopping anything already using port 8010...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8010 " ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1

echo.
echo Starting TrueCost backend on http://localhost:8010
echo (reads settings from ..\.env: Gemini key, Tiger Data database)
echo Check it in a browser: http://localhost:8010/health   and   http://localhost:8010/docs
echo.
"%PY%" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8010
pause
