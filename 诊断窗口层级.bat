@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请在 5 秒内保持 UU远程 隐私屏开启，随后将自动打印 UU远程 隐私屏与微信输入法的 Z-Band 层级...
timeout /t 5 /nobreak >nul
python app.py --inspect-zorder
echo.
pause
