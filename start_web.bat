@echo off
echo ===================================================
echo   Starting TuGou Bot System
echo ===================================================

cd /d "%~dp0"

:: Check if bsc_bot\venv exists
if not exist bsc_bot\venv (
    echo Error: Virtual environment not found in bsc_bot\venv!
    echo Please create it or ensure it exists.
    pause
    exit
)

echo [1/3] Starting Backend API Server...
start "TuGou Backend API" cmd /k "bsc_bot\venv\Scripts\python.exe -m uvicorn web.api:app --host 0.0.0.0 --port 8002 --reload"

echo Waiting for backend to initialize...
timeout /t 5 /nobreak >nul

echo [2/3] Starting Frontend Interface...
cd frontend
if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install
)
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
