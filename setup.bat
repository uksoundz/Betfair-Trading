@echo off
REM Double-click to enter your API keys (saved to .env in this folder).
cd /d "%~dp0"
py -m tradescout.cli setup
pause
