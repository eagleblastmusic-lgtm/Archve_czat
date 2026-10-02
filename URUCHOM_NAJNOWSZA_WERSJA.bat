@echo off
setlocal
cd /d "%~dp0"
title Archivebate - aktualna wersja (przegladarka)

if not exist "%~dp0run.py" (
    echo [START] Nie znaleziono run.py w katalogu projektu:
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

echo [START] Uruchamiam aktualny runtime aplikacji przez run.py.
echo [START] Port 8000 jest sprawdzany bezpiecznie; obce procesy nie sa konczone.
python "%~dp0run.py"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" echo [START] Aplikacja zakonczyla sie kodem %APP_EXIT%.
pause
exit /b %APP_EXIT%
