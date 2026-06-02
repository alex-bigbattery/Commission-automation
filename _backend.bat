@echo off
title Big Battery - Backend API
cd /d "%~dp0"
echo Backend starting at http://127.0.0.1:8000 ...
echo.
".venv\Scripts\uvicorn.exe" backend.app:app --host 127.0.0.1 --port 8000
echo.
echo === Backend stopped ===
pause
