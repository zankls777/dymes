@echo off
setlocal EnableExtensions
title BOM3 Data Management System
cd /d "%~dp0"

rem ============================================================
rem  BOM3 Data Management System - One-click Launcher (Windows)
rem
rem  Why this file exists: on this machine the .py file association points
rem  to "C:\Windows\py.exe", which does not exist. Double-clicking app.py
rem  therefore makes Windows fail before Python ever starts, so the console
rem  flashes and vanishes. This launcher does NOT depend on that association
rem  or on the `py` launcher: it probes real interpreters in a fixed order and
rem  verifies the full dependency set before starting. Every failure path ends
rem  with `pause`, so the window never closes silently.
rem
rem  IMPORTANT: keep this file pure ASCII. Chinese text inside a .bat
rem  combined with chcp 65001 makes cmd.exe lose byte sync and execute
rem  comment fragments as commands.
rem ============================================================
echo ============================================================
echo   BOM3 Data Management System - Launcher
echo ============================================================
echo.

rem ---------- [1/4] Locate a usable Python interpreter ----------
rem Candidate order:
rem   1) project .venv             (most reproducible)
rem   2) python on PATH            (must NOT be the Microsoft Store alias)
rem   3) py -3.12 (python launcher, only if it really exists)
rem   4) python.org per-user installs (Python313 .. Python310)
rem Each candidate must import the full dependency set; the Store alias and
rem bare interpreters are rejected here instead of failing later at startup.
set "PROBE=import flask,flask_sqlalchemy,flask_wtf,flask_login,flask_caching,flask_migrate,sqlalchemy,waitress,openpyxl,pandas,numpy,requests,ezdxf"
set "PYEXE="
set "PYARG="

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "%PROBE%" >nul 2>nul
    if not errorlevel 1 set "PYEXE=%~dp0.venv\Scripts\python.exe"
)

if not defined PYEXE (
    python -c "%PROBE%" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    py -3.12 -c "%PROBE%" >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=py"
        set "PYARG=-3.12"
    )
)

if not defined PYEXE (
    for %%D in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" "%ProgramFiles%\Python313\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles%\Python311\python.exe" "%ProgramFiles%\Python310\python.exe") do (
        if not defined PYEXE if exist "%%~D" (
            "%%~D" -c "%PROBE%" >nul 2>nul
            if not errorlevel 1 set "PYEXE=%%~D"
        )
    )
)

if not defined PYEXE (
    echo [ERROR] No usable Python interpreter found.
    echo.
    echo         Need Python 3.10+ with the packages from requirements.txt.
    echo         Install from https://www.python.org/downloads/windows/
    echo         then run: python -m pip install -r requirements.txt
    echo.
    echo         Do NOT use the Microsoft Store Python: its alias cannot see
    echo         this project's packages and exits immediately when launched.
    goto :fail
)

echo [1/4] Interpreter selected: %PYEXE% %PYARG%
"%PYEXE%" %PYARG% -c "import sys;print('       version:', sys.version.split()[0]);print('       exe    :', sys.executable)"

rem ---------- [2/4] Dependencies ----------
rem Already verified by the probe above; this line just confirms it in the log.
"%PYEXE%" %PYARG% -c "%PROBE%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Dependencies missing for %PYEXE% %PYARG%
    echo         Run: %PYEXE% %PYARG% -m pip install -r requirements.txt
    goto :fail
)
echo [2/4] Dependencies: OK

rem ---------- [3/4] Free the port (stop a stale instance) ----------
if defined APP_PORT (set "PORT=%APP_PORT%") else (set "PORT=5004")
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 (
    echo [3/4] Port %PORT%: free
) else (
    echo [3/4] Port %PORT% is busy, stopping the old instance...
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do taskkill /F /PID %%P >nul 2>nul
    timeout /t 2 >nul
    echo       Old instance stopped.
)

rem ---------- [4/4] Start the service ----------
rem Production mode: Waitress, single process, no reloader. The server runs
rem in THIS window, so a crash stays visible and Ctrl+C stops it cleanly.
if not defined FLASK_CONFIG set "FLASK_CONFIG=production"

echo [4/4] Starting app.py (mode: %FLASK_CONFIG%)...
echo.
echo   URL   : http://localhost:%PORT%
echo   Log   : logs\dayan.log
echo   Admin : see instance\admin_initial_password.txt
echo.
echo   Keep this window open while using the system.
echo   Press Ctrl+C to stop the service.
echo.

rem Open the browser a few seconds later, once the server is listening.
rem (URL is unquoted on purpose: `start "url"` would treat it as a window title.)
start "" cmd /c "timeout /t 8 >nul & start http://localhost:%PORT%"

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
"%PYEXE%" %PYARG% app.py
if errorlevel 1 goto :fail

echo.
echo Service stopped.
pause
exit /b 0

:fail
echo.
echo Startup failed. Read the message above, fix it, then run this file again.
pause
exit /b 1
