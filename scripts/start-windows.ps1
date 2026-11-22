param([ValidateRange(1,65535)][int]$Port = 8000)

. (Join-Path $PSScriptRoot 'windows-environment.ps1')
$paths = Get-FinanceWindowsPaths
Set-FinanceWindowsEnvironment $paths
Set-Location -LiteralPath $paths.Root

try {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    $listener.ExclusiveAddressUse = $true
    try { $listener.Start() } finally { $listener.Stop() }
} catch {
    Write-Host "Port $Port is already in use. Stop the existing Windows or WSL service, then retry." -ForegroundColor Red
    exit 1
}
if (!(Test-FinanceWindowsEnvironment $paths)) {
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup-windows.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host 'Starting the ledger with project-local Windows Python...'
Write-Host "Open http://127.0.0.1:$Port in your browser."
Write-Host 'Press Ctrl+C or close this window to stop the service.'
& $paths.Python -s -m uvicorn app.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
