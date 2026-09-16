$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = 'C:\Python314\python.exe'
if (-not (Test-Path $python)) {
    $cmd = Get-Command python -ErrorAction Stop
    $python = $cmd.Source
}

function Get-Port8000Listener {
    try {
        return Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction Stop | Select-Object -First 1
    }
    catch {
        $line = netstat -ano -p tcp | Select-String -Pattern '^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$' | Select-Object -First 1
        if (-not $line) { return $null }
        return [pscustomobject]@{ OwningProcess = [int]$line.Matches[0].Groups[1].Value }
    }
}

$listener = Get-Port8000Listener
if ($listener) {
    $pid8000 = [int]$listener.OwningProcess
    $proc = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
    $procName = if ($proc) { $proc.ProcessName } else { 'nieznany proces' }
    Write-Host ''
    Write-Host '[START] Port 8000 jest juz zajety.' -ForegroundColor Red
    Write-Host ("[START] PID: {0} | Proces: {1}" -f $pid8000, $procName) -ForegroundColor Yellow
    Write-Host '[START] Zamknij poprzednia instancje Archivebate / jej okno serwera i uruchom plik ponownie.' -ForegroundColor Yellow
    Write-Host ("[START] Do sprawdzenia procesu: Get-Process -Id {0}" -f $pid8000) -ForegroundColor DarkGray
    exit 2
}

Write-Host '[START] Uruchamiam nowy serwer Archivebate V4.3...' -ForegroundColor Cyan
$serverCommand = ('"{0}" browser_server.py' -f $python)
$server = Start-Process -FilePath $env:ComSpec -ArgumentList @('/k', $serverCommand) -WorkingDirectory $PSScriptRoot -PassThru

$ready = $false
for ($i = 1; $i -le 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/runtime/v43' -TimeoutSec 1
        if ($r.runtime -eq 'v4.3-fast2' -and $r.grouped_fast_path_v2 -eq $true) {
            $ready = $true
            break
        }
    }
    catch {
        # Server is still starting. Retry until timeout below.
    }
}

if (-not $ready) {
    Write-Host ''
    Write-Host '[START] Serwer nie zglosil poprawnego runtime V4.3 w ciagu 20 s.' -ForegroundColor Red
    Write-Host '[START] Sprawdz okno "Archivebate Browser Server" - powinien byc tam konkretny blad startu.' -ForegroundColor Yellow
    exit 3
}

Write-Host '[START] Runtime potwierdzony: v4.3-fast2 + grouped_fast_path_v2=true' -ForegroundColor Green
Write-Host '[START] Otwieram http://127.0.0.1:8000/' -ForegroundColor Green
Start-Process 'http://127.0.0.1:8000/'
exit 0
