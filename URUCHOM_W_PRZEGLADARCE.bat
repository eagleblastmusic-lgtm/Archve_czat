@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0URUCHOM_W_PRZEGLADARCE.ps1"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" echo [START] Launcher zakonczyl sie kodem %EXITCODE%.
endlocal & exit /b %EXITCODE%
