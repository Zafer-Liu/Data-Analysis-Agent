@echo off
:: Re-launch in a persistent cmd window so the window stays open on error.
if "%~1"=="--relaunched" goto :main
start "Business Analytics Agent" cmd /k "%~f0" --relaunched
exit /b 0

:main
title Business Analytics Agent
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot switch to app directory: %~dp0
    pause
    exit /b 1
)

if not exist "app.py" (
    echo [ERROR] app.py not found in: %CD%
    pause
    exit /b 1
)

set "APP_FILE=%CD%\app.py"
set "PORT=5001"
set "REQUIREMENTS_FILE=%CD%\requirements.txt"
set "VENV_DIR=%CD%\.venv"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

echo ============================================
echo   Business Analytics Agent
echo ============================================
echo.

rem ============================================================
rem  Detect system Python (python / py -3)
rem ============================================================
set "PY_CMD="
set "SYS_PY="

where python >nul 2>&1
if not errorlevel 1 (
    python --version >nul 2>&1
    if not errorlevel 1 set "SYS_PY=python"
)
if not defined SYS_PY (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 --version >nul 2>&1
        if not errorlevel 1 set "SYS_PY=py -3"
    )
)
if not defined SYS_PY (
    echo [ERROR] No Python found.
    echo         Please install Python 3.10+ from https://www.python.org/downloads/
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ============================================================
rem  Create / use virtual environment
rem ============================================================
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %SYS_PY% -m venv "%VENV_DIR%"
    if errorlevel 1 ( echo [ERROR] Failed to create venv & pause & exit /b 1 )
)
set "PY_CMD=%VENV_DIR%\Scripts\python.exe"

rem Ensure pip
"%PY_CMD%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] pip not found, bootstrapping...
    "%PY_CMD%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo [ERROR] pip is not available and cannot be bootstrapped.
        pause
        exit /b 1
    )
)

rem ============================================================
rem  Install dependencies (first run may take a few minutes)
rem ============================================================
echo [INFO] Checking dependencies (first run may take a few minutes)...
set "PYTHONUTF8=1"
"%PY_CMD%" -m pip install -r "%REQUIREMENTS_FILE%" --no-deps 2>nul
"%PY_CMD%" -m pip install -r "%REQUIREMENTS_FILE%"
if errorlevel 1 (
    echo [WARN] Retrying with mirror...
    "%PY_CMD%" -m pip install -r "%REQUIREMENTS_FILE%" -i "%PIP_MIRROR%"
    if errorlevel 1 ( echo [ERROR] Failed to install dependencies & pause & exit /b 1 )
)

rem Check port
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [ERROR] Port %PORT% already in use (PID=%%a^)
    pause
    exit /b 1
)

rem Launch
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo.
echo [INFO] Starting... Browser will open at http://127.0.0.1:%PORT%
echo [INFO] Close this window to stop the app.
echo.
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:%PORT%"
"%PY_CMD%" "%APP_FILE%"

if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] App exited with code %ERRORLEVEL%
)
pause
