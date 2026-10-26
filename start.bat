@echo off
rem ============================================================
rem  BOM3 Data Management System - One-click Launcher (Windows)
rem  Double-click to start the service and open the browser.
rem  Requires: Python 3.10+ and packages from requirements.txt
rem
rem  IMPORTANT: keep this file pure ASCII. Chinese text in a .bat
rem  file combined with chcp 65001 makes cmd.exe lose byte-sync,
rem  executing comment fragments as commands (window may flash
rem  and exit). Use English only in this script.
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

rem ---------- [1/4] Check Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Please install Python 3.10+ first.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys;print(sys.version_info>= (3,10))"') do set PY_OK=%%i
if "%PY_OK%"=="False" (
    echo [ERROR] Python 3.10 or newer is required.
    python --version
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python -c "import sys;print('%%d.%%d.%%d' %% sys.version_info[:3])"') do set PY_VER=%%i
echo [1/4] Python: OK (%PY_VER%)

rem ---------- [2/4] Check dependencies ----------
rem Verify every direct dependency from requirements.txt; any missing
rem module breaks app.py at startup or silently degrades features.
rem Import-name mapping: opencv -^> cv2, Pillow -^> PIL,
rem rapidocr-onnxruntime -^> rapidocr_onnxruntime
set MISSING=0
for %%M in (flask flask_sqlalchemy flask_wtf flask_login flask_caching flask_migrate sqlalchemy waitress werkzeug numpy pandas dateutil pydantic requests aiofiles openpyxl xlrd ezdxf cv2 PIL) do (
    python -c "import %%M" >nul 2>nul
    if errorlevel 1 (
        echo        [%%M] NOT installed
        set MISSING=1
    )
)
rem OCR stack checked separately (package name differs from import name)
python -c "import zxingcpp" >nul 2>nul || (echo        [zxing-cpp] NOT installed & set MISSING=1)
python -c "import rapidocr_onnxruntime" >nul 2>nul || (echo        [rapidocr-onnxruntime] NOT installed & set MISSING=1)
python -c "import onnxruntime" >nul 2>nul || (echo        [onnxruntime] NOT installed & set MISSING=1)
if "%MISSING%"=="1" (
    echo [ERROR] Missing dependencies. Run: py -3.12 -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo [2/4] Dependencies: OK

rem ---------- [3/4] Port check ----------
rem Must match PORT in app.py (default 5004); override via APP_PORT.
if not defined APP_PORT (set PORT=5004) else (set PORT=%APP_PORT%)
set PORT_BUSY=0
for /f "tokens=2" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do set PORT_BUSY=1
if "%PORT_BUSY%"=="1" (
    echo [3/4] Port %PORT% is busy, killing the old process...
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
        taskkill /F /PID %%P >nul 2>nul
    )
    timeout /t 2 >nul
    echo        Old process terminated.
) else (
    echo [3/4] Port %PORT%: free
)

rem ---------- [4/4] Start service ----------
rem Production mode by default (Waitress, single process, no reloader)
rem to avoid create_app() running twice and double cold-start cost.
rem For hot-reload debugging, set FLASK_CONFIG=development first.
if not defined FLASK_CONFIG set FLASK_CONFIG=production
echo [4/4] Starting app.py (mode: %FLASK_CONFIG%)...
rem cmd /k keeps the server window open so a startup crash stays visible
rem instead of flashing and disappearing.
start "BOM3 Server" cmd /k "python app.py"
echo.
echo ============================================================
echo   BOM3 Data Management System started
echo.
echo   URL      : http://localhost:%PORT%
echo   Admin    : admin / admin123!
echo   Log file : logs/dayan.log
echo ============================================================
echo.
echo Opening browser...
timeout /t 3 >nul
start "" http://localhost:%PORT%
pause
