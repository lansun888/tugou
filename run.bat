@echo off
setlocal

:: Set PYTHONPATH to current directory
set PYTHONPATH=%cd%

:: Activate virtual environment if exists (assuming standard names)
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

:: Run the bot with API
python web/api.py

endlocal
pause
