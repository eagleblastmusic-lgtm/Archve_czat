@echo off
setlocal
cd /d "%~dp0"
title Archivebate - aktualna wersja (desktop)

if not exist "%~dp0desktop_app.py" (
    echo [START] Nie znaleziono desktop_app.py w katalogu projektu:
    echo         %~dp0
    pause
    exit /b 2
)

where python >nul 2>nul
if errorlevel 1 (
    echo [START] Nie znaleziono polecenia python w PATH.
    echo         Zainstaluj Python i wymagania projektu przez INSTALL_DEPENDENCIES.bat.
    pause
    exit /b 9009
)

echo [START] Uruchamiam aktualne okno desktopowe przez desktop_app.py.
echo [START] Desktop korzysta z runtime_app.py i nie konczy obcych procesow.
python "%~dp0desktop_app.py"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" echo [START] Aplikacja zakonczyla sie kodem %APP_EXIT%.
pause
exit /b %APP_EXIT%
