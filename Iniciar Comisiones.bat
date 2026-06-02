@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Big Battery - Commission System
color 0B

REM ============================================================
REM   Big Battery - Commission System
REM   Starts backend (API) + frontend (UI) and opens browser
REM ============================================================

cd /d "%~dp0"

echo.
echo   ============================================================
echo    BIG BATTERY - COMMISSION SYSTEM
echo   ============================================================
echo.
echo    Starting application for Accounting...
echo    (first run may take an extra 1-2 minutes)
echo.

REM ---------- Check Python (.venv) ----------
if not exist ".venv\Scripts\uvicorn.exe" (
    color 0C
    echo.
    echo   [ERROR] uvicorn was not found in .venv\Scripts\
    echo   Contact the technical team to reinstall the Python environment.
    echo.
    pause
    exit /b 1
)

REM ---------- Check node_modules ----------
if not exist "frontend\node_modules" (
    echo   [INFO] First run - installing frontend dependencies...
    echo         This may take 1-2 minutes. It runs only once.
    echo.
    pushd frontend
    call npm install
    if errorlevel 1 (
        color 0C
        echo.
        echo   [ERROR] Failed to install frontend dependencies.
        popd
        pause
        exit /b 1
    )
    popd
    echo   [OK] Dependencies installed.
    echo.
)

REM ---------- Check helper files ----------
if not exist "_backend.bat" (
    color 0C
    echo   [ERROR] Missing file: _backend.bat
    pause
    exit /b 1
)
if not exist "_frontend.bat" (
    color 0C
    echo   [ERROR] Missing file: _frontend.bat
    pause
    exit /b 1
)

REM ---------- Backend ----------
echo   [1/3] Backend (API)...

curl.exe -fsS "http://localhost:8000/api/health" >nul 2>&1
if %errorlevel% equ 0 (
    echo         Already running on port 8000. [OK]
) else (
    start "" /MIN "%~dp0_backend.bat"
    echo         Waiting for startup...

    set /a "attempts=0"
    :wait_backend
    set /a "attempts+=1"
    if !attempts! gtr 60 (
        color 0C
        echo.
        echo   [ERROR] Backend did not respond within 60 seconds.
        echo   Open the minimized window "Big Battery - Backend API"
        echo   to see the error message.
        pause
        exit /b 1
    )
    timeout /t 1 /nobreak >nul
    curl.exe -fsS "http://localhost:8000/api/health" >nul 2>&1
    if errorlevel 1 goto wait_backend
    echo         [OK] Backend ready at http://localhost:8000
)

REM ---------- Frontend ----------
echo   [2/3] Frontend (UI)...

curl.exe -fsS "http://localhost:5173" | findstr /I /C:"Big Battery Commission Viewer" >nul 2>&1
if %errorlevel% equ 0 (
    echo         Already running on port 5173. [OK]
) else (
    start "" /MIN "%~dp0_frontend.bat"
    echo         Waiting for startup...

    set /a "attempts2=0"
    :wait_frontend
    set /a "attempts2+=1"
    if !attempts2! gtr 60 (
        color 0C
        echo.
        echo   [ERROR] Frontend did not respond within 60 seconds.
        echo   Open the minimized window "Big Battery - Frontend UI"
        echo   to see the error message.
        pause
        exit /b 1
    )
    timeout /t 1 /nobreak >nul
    curl.exe -fsS "http://localhost:5173" | findstr /I /C:"Big Battery Commission Viewer" >nul 2>&1
    if errorlevel 1 goto wait_frontend
    echo         [OK] Frontend ready at http://localhost:5173
)

REM ---------- Open browser ----------
echo   [3/3] Opening browser...
timeout /t 2 /nobreak >nul
start "" "http://localhost:5173"

color 0A
echo.
echo   ============================================================
echo    APPLICATION READY
echo   ============================================================
echo.
echo    URL:  http://localhost:5173
echo.
echo    If the browser did not open automatically, copy and paste
echo    this address in your browser (Chrome, Edge, Firefox).
echo.
echo   ============================================================
echo    TO CLOSE THE APPLICATION:
echo   ============================================================
echo.
echo    Run this file: "Detener Comisiones.bat"
echo.
echo   ============================================================
echo.
echo   You can close this window at any time
echo   (the application will keep running).
echo.
pause
