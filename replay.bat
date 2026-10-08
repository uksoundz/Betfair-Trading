@echo off
REM Double-click to replay a past Saturday from the built-in data and see how the picks did.
cd /d "%~dp0"
py -m tradescout.cli scan --date 2025-11-08 --show-results --html replay.html --top 20
if exist replay.html start replay.html
pause
