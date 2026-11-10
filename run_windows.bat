@echo off
cd /d "%~dp0"
python -m pip install -U -r requirements.txt
python check_install.py
if errorlevel 1 (
  echo.
  echo Fix the problems above, then run this file again.
  pause
  exit /b 1
)
python -m streamlit run app.py
pause
