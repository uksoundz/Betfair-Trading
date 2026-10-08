@echo off
REM Runs TradeScout even if "tradescout" is not on your PATH. Usage: tradescout scan ...
cd /d "%~dp0"
py -m tradescout.cli %*
