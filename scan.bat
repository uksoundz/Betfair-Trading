@echo off
REM Double-click to rank today's matches and open the web page report.
cd /d "%~dp0"
py -m tradescout.cli scan --html today.html --top 30
if exist today.html start today.html
pause
