@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0deployment\run_cloud.ps1" %*
pause
