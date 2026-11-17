@echo off
echo.
echo ========================================
echo   AI Providers System - Dependencies Installer
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
echo [INFO] Installing core dependencies...
echo.

REM Install core dependencies
pip install fastapi uvicorn pydantic pydantic-settings python-dotenv aiohttp httpx requests

if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Core dependencies installed successfully!
echo [INFO] You can now run START_SERVER.bat to start the system
echo.
pause