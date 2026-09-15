@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

rem The desktop app also uses port 8000. Close it before running this launcher.
start "Archivebate Browser Server" cmd /k ""%PYTHON_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000/"

endlocal
