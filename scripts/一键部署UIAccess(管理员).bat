@echo off
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = (Resolve-Path '.\build_config\deploy_uiaccess.ps1').Path; Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',('\"' + $p + '\"')"
