@echo off
setlocal
chcp 65001 >nul

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-wsl.ps1"

echo.
echo The service has stopped.
pause
