@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath 'pythonw.exe' -ArgumentList '\"%~dp0..\src\app.py\"' -Verb RunAs"
    exit /b
)
start "" pythonw.exe "%~dp0..\src\app.py"
