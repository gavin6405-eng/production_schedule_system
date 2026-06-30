@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安裝或確認必要套件...
python -m pip install -r requirements.txt
echo [2/2] 啟動生產排程系統...
python -m streamlit run app.py
pause
