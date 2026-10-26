@echo off
REM ============================================================
REM  BOM3 一键打包并投递到香港目录（Windows）
REM  兼容 Win7 / Win10
REM
REM  用法:
REM    pack_to_hk.bat                          使用默认/环境变量目标
REM    pack_to_hk.bat "Z:\香港"                指定目标目录
REM    set BOM3_HK_DIR=Z:\香港 & pack_to_hk.bat 用环境变量
REM ============================================================
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."

if not "%~1"=="" (
    python "%SCRIPT_DIR%pack_to_hk.py" --dest "%~1"
) else (
    python "%SCRIPT_DIR%pack_to_hk.py"
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [错误] 脚本执行失败，退出码 %RC%
)
echo.
pause
endlocal
exit /b %RC%
