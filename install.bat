@echo off
rem Double-click to install LLM Mascot. Options: -Startup (start with Windows), -DownloadModel
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
