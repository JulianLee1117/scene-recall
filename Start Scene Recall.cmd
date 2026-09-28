@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-scene-recall.ps1" %*
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "http://localhost:3000/"
