@echo off
setlocal
cd /d %~dp0

REM Preferir Python 3.12 si está instalado; si no, usar el Python disponible.
set "PYTHON_CMD=python"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.12 -c "import sys" >nul 2>nul
    if %errorlevel%==0 set "PYTHON_CMD=py -3.12"
)

if not exist .venv\Scripts\python.exe (
    %PYTHON_CMD% -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m streamlit run app.py
pause
