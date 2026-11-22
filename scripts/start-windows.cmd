@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
set "FINANCE_EXIT_CODE=%ERRORLEVEL%"
echo.
echo The service has stopped. Exit code: %FINANCE_EXIT_CODE%
pause
exit /b %FINANCE_EXIT_CODE%
