@echo off
title Big Battery - Frontend UI
cd /d "%~dp0\frontend"
echo Frontend starting at http://127.0.0.1:5173 ...
echo.
call npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
echo.
echo === Frontend stopped ===
pause
