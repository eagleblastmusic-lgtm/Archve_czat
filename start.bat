@echo off
title Archivebate Video Browser
cd /d "%~dp0"

echo ============================================================
echo      ARCHIVEBATE VIDEO BROWSER
echo ============================================================
echo [1/2] Sprawdzanie bibliotek Pythona...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 exit /b 1

echo [2/2] Startowanie serwera i otwieranie przegladarki...
echo Port 8000 jest sprawdzany bezpiecznie przez run.py; obce procesy nie sa zabijane.
python run.py
pause
