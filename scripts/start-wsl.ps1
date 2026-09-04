$projectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$linuxProjectDir = (& wsl.exe -e wslpath -a $projectDir).Trim()

if ([string]::IsNullOrWhiteSpace($linuxProjectDir)) {
    throw "Unable to resolve the WSL project path."
}

Write-Host "Starting personal finance ledger..."
Write-Host "Open http://127.0.0.1:8000 in your browser."
Write-Host "Press Ctrl+C in this window to stop the service."
Write-Host ""

$bashCommand = "cd `"$linuxProjectDir`" && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
& wsl.exe -e bash -lc $bashCommand
