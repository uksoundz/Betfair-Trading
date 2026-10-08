@echo off
REM Double-click this once. Installs Python (if needed) and TradeScout.
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Making sure Python 3 is installed...
py install 3 -y >nul 2>nul
echo Installing TradeScout...
py -m pip install -e . || py -3 -m pip install -e .
if errorlevel 1 (
  echo.
  echo Install failed. Scroll up for the error, then ask for help.
) else (
  echo.
  echo Done. Now double-click scan.bat, or type:  tradescout scan --date 2025-11-08 --show-results
)
pause
