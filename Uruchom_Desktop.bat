@echo off
title Archivebate Desktop Pro
cd /d "%~dp0"

echo ============================================================
echo      ARCHIVEBATE ^& CAMWHORES DESKTOP PRO
echo ============================================================
echo Port 8000 jest sprawdzany bezpiecznie przez desktop_app.py.
echo Obce procesy nie sa automatycznie zabijane.
python -m pip install -r requirements.txt --quiet
if errorlevel 1 exit /b 1
python desktop_app.py
pause
