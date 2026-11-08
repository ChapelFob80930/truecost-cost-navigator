@echo off
rem Starts the TrueCost backend AND a free Cloudflare quick tunnel (no account needed),
rem so the published Lovable site can reach your PC through a public https address.
rem Leave both windows open while you demo. Close them to stop. Anyone with the address can use the backend.
setlocal
cd /d "%~dp0"

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo cloudflared is not installed or not on PATH.
  echo   1. Install:  winget install --id Cloudflare.cloudflared
  echo   2. Close and reopen this window, then run this file again.
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

echo.
echo Backend is up. Opening the tunnel...
echo Look for a line like:   https://something-random.trycloudflare.com
echo COPY that address into Lovable (no trailing slash). It changes every time you run this.
echo.
cloudflared tunnel --url http://localhost:8010
pause
