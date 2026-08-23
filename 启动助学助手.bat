@echo off
chcp 65001 >nul
title AI Study Assistant - Launcher
setlocal

set "ROOT=%~dp0"

echo ============================================
echo   AI Study Assistant - Launcher
echo ============================================
echo.

if not exist "%ROOT%backend\venv\Scripts\python.exe" (
    echo [ERROR] Backend venv not found:
    echo   %ROOT%backend\venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
    echo [INFO] Installing frontend dependencies, please wait...
    pushd "%ROOT%frontend"
    call "D:\devolop\node\npm.cmd" install
    popd
)

echo [1/2] Starting backend  (FastAPI :8080)...
start "AI-Backend" /D "%ROOT%backend" cmd /k "venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080"

echo [2/2] Starting frontend (Vite :5173)...
start "AI-Frontend" /D "%ROOT%frontend" cmd /k "D:\devolop\node\npm.cmd run dev"

echo.
echo   Backend  : http://127.0.0.1:8080/health
echo   Frontend : http://localhost:5173
echo.
echo   Accounts: admin/123456 (admin), user25/123456 (learner)
echo.
echo   Two service windows will stay open. Close them to stop.
echo   You can close THIS window now.
echo.
start "" http://localhost:5173
timeout /t 10
endlocal
