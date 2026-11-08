@echo off
rem Starts the TrueCost backend AND an ngrok tunnel, so the published Lovable site can reach your PC.
rem Needs ngrok installed and signed in once:   ngrok config add-authtoken YOUR_TOKEN   (token from dashboard.ngrok.com)
rem Leave both windows open while you demo. Close them to stop. Anyone with the ngrok address can use the backend.
setlocal
cd /d "%~dp0"

where ngrok >nul 2>&1
if errorlevel 1 (
  echo ngrok is not installed or not on PATH.
  echo   1. Install:  winget install ngrok.ngrok     ^(or download from https://ngrok.com/download^)
  echo   2. Sign up free at https://dashboard.ngrok.com, copy your authtoken, then run:
  echo        ngrok config add-authtoken YOUR_TOKEN
  echo   3. Run this file again.
  pause
  exit /b 1
)

echo Starting the backend in its own window...
start "TrueCost backend" cmd /c "%~dp0start_backend.bat"

echo Waiting for the backend to answer...
set /a tries=0
:wait
set /a tries+=1
curl -s -o nul http://localhost:8010/health
if errorlevel 1 (
  if %tries% GEQ 30 ( echo Backend did not start. Check the "TrueCost backend" window. & pause & exit /b 1 )
  timeout /t 1 >nul
  goto wait
)
echo Backend is up. Opening the tunnel...
echo.
echo COPY the https "Forwarding" address below into Lovable (without a trailing slash).
echo.
ngrok http 8010
pause
