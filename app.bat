@echo off
REM Double-click to open TradeScout in your browser. Keep this window open while you use it.
cd /d "%~dp0"
py -m tradescout.cli app
if errorlevel 1 (
  echo.
  echo TradeScout did not start. If the message above says "No module named" anything,
  echo double-click install.bat again, then try app.bat once more.
)
pause
