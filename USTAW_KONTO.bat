@echo off
setlocal
cd /d "%~dp0"
title Konfiguracja konta Archivebate

echo ============================================================
echo      KONFIGURACJA KONTA ARCHIVEBATE
echo ============================================================
echo Dane zostana zapisane lokalnie w .env.local.
echo Plik nie jest dodawany do Git.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$email=Read-Host 'Email'; $secure=Read-Host 'Haslo' -AsSecureString; $bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure); try {$pass=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)} finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)}; $lines=@('ARCHIVEBATE_EMAIL='+$email,'ARCHIVEBATE_PASSWORD='+$pass); [IO.File]::WriteAllLines((Join-Path (Get-Location) '.env.local'), $lines, (New-Object Text.UTF8Encoding($false))); Write-Host ''; Write-Host 'Zapisano .env.local w UTF-8 bez BOM.' -ForegroundColor Green"

echo.
echo Mozesz teraz uruchomic URUCHOM_PROGRAM.bat.
pause
