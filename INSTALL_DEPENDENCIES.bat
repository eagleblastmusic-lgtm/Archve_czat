@echo off
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.lock.txt
python -m pip check
pause
