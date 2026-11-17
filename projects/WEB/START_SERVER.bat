@echo off
echo.
echo ========================================
echo   AI Providers System - Quick Start
echo   (C) 2026 SAYED_ELAZAMEY - All Rights Reserved
echo   PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and try again
    pause
    exit /b 1
)

echo [INFO] Python found
echo [INFO] Starting AI Providers System...
echo.

REM Start the server
python main.py

echo.
echo [INFO] Server stopped
pause