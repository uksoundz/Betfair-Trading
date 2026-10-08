@echo off
REM Double-click to open TradeScout in your browser. Keep this window open while you use it.
cd /d "%~dp0"
py -m tradescout.cli app
pause
