# Starts Claude Remote Control for D:\RealF1-esports while F1 25 is running.
# When the game closes it first turns capture and the engineer OFF CLEANLY (so the session
# is closed and saved), then stops Remote Control.
# Started at logon by the scheduled task "RealF1 esports F1 Watcher". Copy of pc/f1-watcher.ps1.
$gameProcess = 'F1_25'
$claude  = "$env:USERPROFILE\.local\bin\claude.exe"
$workDir = 'D:\RealF1-esports'
$engDir  = 'D:\RealF1-esports\engineer'
$log     = 'D:\RealF1-esports\engineer\run\watcher.log'

function Write-Log($msg) {
    New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
    Add-Content -Path $log -Value "$(Get-Date -Format s) $msg"
}

function Get-RemoteControl {
    # Only the host process: its command line contains 'remote-control'.
    @(Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
        Where-Object { $_.CommandLine -match 'remote-control' })
}

$wasRunning = $false
while ($true) {
    $gameRunning = [bool](Get-Process -Name $gameProcess -ErrorAction SilentlyContinue)
    $rc = Get-RemoteControl

    if ($gameRunning -and $rc.Count -eq 0) {
        Write-Log 'Game running and no Remote Control: starting it'
        Start-Process -FilePath $claude -ArgumentList 'remote-control', '--permission-mode', 'auto' `
                      -WorkingDirectory $workDir -WindowStyle Minimized
    }
    elseif (-not $gameRunning -and $wasRunning) {
        Write-Log 'Game closed: turning capture and the engineer off'
        Push-Location $engDir
        $out = & python -m race_engineer.control off --force 2>&1 | Out-String
        Pop-Location
        Write-Log $out.Trim()
        foreach ($p in $rc) { taskkill.exe /PID $p.ProcessId /T /F | Out-Null }
        Write-Log 'Remote Control stopped'
    }
    $wasRunning = $gameRunning
    Start-Sleep -Seconds 10
}
