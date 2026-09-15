@echo off
title Archivebate Video Browser
cd /d "%~dp0"

echo ============================================================
echo      ARCHIVEBATE VIDEO BROWSER
echo ============================================================
echo Startowanie serwera i otwieranie przegladarki...
echo Port 8000 jest sprawdzany bezpiecznie przez run.py; obce procesy nie sa zabijane.
python run.py
pause
