# Compatibility entry point for existing shortcuts. Runs Windows Python directly.
& (Join-Path $PSScriptRoot 'start-windows.ps1') @args
exit $LASTEXITCODE
