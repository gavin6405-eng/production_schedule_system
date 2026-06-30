@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo 安裝完成，請雙擊「啟動系統.bat」。
pause
