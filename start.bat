@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_EXE=%USERPROFILE%\anaconda3\python.exe"
if exist "%PY_EXE%" (
    set "PATH=%USERPROFILE%\anaconda3;%USERPROFILE%\anaconda3\Scripts;%USERPROFILE%\anaconda3\Library\bin;%PATH%"
) else (
    set "PY_EXE=python"
)

echo ============================================================
echo   Unreal Engine 5.8 C++ API Reference Server
echo   Address: http://127.0.0.1:5858
echo   (Wait 1-2s; browser opens automatically once ready.)
echo   (Keep this window open while using the documentation.)
echo ============================================================
echo.

"%PY_EXE%" -m server.app --open-browser
echo.
echo Server stopped. Press any key to close...
pause >nul
