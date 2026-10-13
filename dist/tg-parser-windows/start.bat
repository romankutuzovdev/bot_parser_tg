@echo off
cd /d "%~dp0"
title TG Parser
echo Starting parser...
python\python.exe parser.py
echo.
pause
