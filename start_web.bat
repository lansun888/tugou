@echo off
echo ===================================================
echo   Starting TuGou Bot System
echo ===================================================

cd /d "%~dp0"

:: Check if .venv exists
if not exist .venv (
    echo Error: Virtual environment not found!
    echo Please run setup.bat first.
    pause
    exit
)

echo [1/3] Starting Backend API Server...
start "TuGou Backend API" cmd /k ".venv\Scripts\python.exe -m uvicorn web.api:app --host 0.0.0.0 --port 8002 --reload"

echo Waiting for backend to initialize...
timeout /t 5 /nobreak >nul

echo [2/3] Starting Frontend Interface...
cd frontend
start "TuGou Frontend" cmd /k "npm run dev -- --port 3000"

echo [3/3] Opening Dashboard...
timeout /t 3 /nobreak >nul
start http://localhost:3000

echo.
echo ===================================================
echo   System Running!
echo   - Frontend: http://localhost:3000
echo   - Backend:  http://localhost:8002
echo   - Swagger:  http://localhost:8002/docs
echo.
echo   Do not close the popup terminal windows.
echo ===================================================
pause
