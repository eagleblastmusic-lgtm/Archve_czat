@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

rem Never silently open an older Archivebate process already listening on port 8000.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue; if ($listener) { Write-Host '[START] Port 8000 jest zajety przez poprzednia instancje. Zamknij jej okno serwera i uruchom ten plik ponownie.' -ForegroundColor Red; exit 2 }"
if errorlevel 1 (
  pause
  exit /b 2
)

start "Archivebate Browser Server" cmd /k ""%PYTHON_EXE%" browser_server.py"

rem Wait until the NEW V4.3 runtime answers. Do not accept an arbitrary process on 8000.
set "READY=0"
for /L %%I in (1,1,20) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/runtime/v43' -TimeoutSec 1; if ($r.runtime -eq 'v4.3-fast2' -and $r.grouped_fast_path_v2 -eq $true) { exit 0 } } catch {}; exit 1" >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :ready
  )
  timeout /t 1 /nobreak >nul
)

echo [START] Serwer V4.3 nie zglosil gotowosci. Sprawdz okno "Archivebate Browser Server".
pause
exit /b 3

:ready
start "" "http://127.0.0.1:8000/"
endlocal
