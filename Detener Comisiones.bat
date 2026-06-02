@echo off
chcp 65001 >nul
title Big Battery - Stop Commissions
color 0E

echo.
echo   ============================================================
echo    BIG BATTERY - Stop Commission System
echo   ============================================================
echo.
echo   Shutting down application...
echo.

set "found=0"

REM ---------- Close windows by title ----------
tasklist /FI "WINDOWTITLE eq Big Battery - Backend API*" 2>nul | find /I "cmd.exe" >nul
if not errorlevel 1 (
    taskkill /FI "WINDOWTITLE eq Big Battery - Backend API*" /T /F >nul 2>&1
    echo   [OK] Backend stopped.
    set "found=1"
) else (
    echo   [..] Backend was not running.
)

tasklist /FI "WINDOWTITLE eq Big Battery - Frontend UI*" 2>nul | find /I "cmd.exe" >nul
if not errorlevel 1 (
    taskkill /FI "WINDOWTITLE eq Big Battery - Frontend UI*" /T /F >nul 2>&1
    echo   [OK] Frontend stopped.
    set "found=1"
) else (
    echo   [..] Frontend was not running.
)

REM ---------- Clean orphaned port processes (only if ports are still occupied) ----------
REM Port 8000 (backend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
    echo   [OK] Orphan process on port 8000 stopped (PID %%a).
    set "found=1"
)
REM Port 5173 (frontend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
    echo   [OK] Orphan process on port 5173 stopped (PID %%a).
    set "found=1"
)
REM Port 5174 (old Vite fallback)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5174 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
    echo   [OK] Orphan process on port 5174 stopped (PID %%a).
    set "found=1"
)

echo.
if "%found%"=="1" (
    color 0A
    echo   Application stopped successfully.
) else (
    color 0E
    echo   No running processes were found.
)
echo.
echo   ============================================================
echo.
timeout /t 3 /nobreak >nul
