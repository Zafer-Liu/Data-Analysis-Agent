@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem =========================================================
rem  Move to BAT directory
rem =========================================================
cd /d "%~dp0"
if errorlevel 1 (
    echo [ERROR] Failed to switch to the script directory.
    pause
    exit /b 1
)

rem =========================================================
rem  Config
rem =========================================================
set "APP_FILE=app.py"
set "REQUIREMENTS_FILE=requirements.txt"
set "VENV_DIR=.venv"
set "PORT=5001"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PY_CMD="

rem =========================================================
rem  Detect Python
rem =========================================================
echo [INFO] Detecting Python...

where python >nul 2>&1
if not errorlevel 1 (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 --version >nul 2>&1
        if not errorlevel 1 set "PY_CMD=py -3"
    )
)

if not defined PY_CMD (
    echo [ERROR] Python 3.8+ is required.
    echo [TIP] Please install Python from:
    echo       https://www.python.org/downloads/
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [INFO] Python command: %PY_CMD%
%PY_CMD% --version
if errorlevel 1 (
    echo [ERROR] Python is detected but cannot run.
    pause
    exit /b 1
)

rem =========================================================
rem  Create venv if missing
rem =========================================================
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [ERROR] Virtual environment activation file missing.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

set "PY_CMD=python"

rem =========================================================
rem  Upgrade pip if needed
rem =========================================================
%PY_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Bootstrapping pip...
    %PY_CMD% -m ensurepip --upgrade
    if errorlevel 1 (
        echo [ERROR] pip is not available and cannot be bootstrapped.
        pause
        exit /b 1
    )
)

rem =========================================================
rem  Install dependencies every run (check and install missing)
rem =========================================================
echo [INFO] Checking and installing dependencies...

if exist "%REQUIREMENTS_FILE%" (
    rem 只安装缺失或未满足版本要求的包，不强制升级已安装的包
    rem 避免每次启动把依赖升级到最新版导致兼容性问题
    %PY_CMD% -m pip install -r "%REQUIREMENTS_FILE%" --no-deps 2>nul
    %PY_CMD% -m pip install -r "%REQUIREMENTS_FILE%"
    if errorlevel 1 (
        echo [WARN] Retry installing dependencies with mirror...
        %PY_CMD% -m pip install -r "%REQUIREMENTS_FILE%" -i %PIP_MIRROR%
        if errorlevel 1 (
            echo [ERROR] Failed to install dependencies from requirements.txt.
            pause
            exit /b 1
        )
    )
) else (
    echo [WARN] requirements.txt not found. No dependency install performed.
)

rem =========================================================
rem  Check app file
rem =========================================================
if not exist "%APP_FILE%" (
    echo [ERROR] Entry file not found: %APP_FILE%
    pause
    exit /b 1
)

rem =========================================================
rem  Check port
rem =========================================================
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [ERROR] Port %PORT% is already in use. PID=%%a
    echo [TIP] Use: tasklist /fi "PID eq %%a"
    pause
    exit /b 1
)

rem =========================================================
rem  Start app and open browser
rem =========================================================

rem Workaround: Python 3.14 bug — some encodings (e.g. mac_turkish) lack
rem IncrementalDecoder, causing charset-normalizer to crash on import.
rem Setting this var tells charset-normalizer to skip encoding-level probing.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo [INFO] Starting application...
echo [INFO] Open: http://127.0.0.1:%PORT%

rem 延迟 2 秒后自动打开浏览器
start "" cmd /c "timeout /t 2 /nobreak >nul && start "" http://127.0.0.1:%PORT%"

%PY_CMD% "%APP_FILE%"
set "RET=%ERRORLEVEL%"

if not "%RET%"=="0" (
    echo [ERROR] Application exited with code %RET%
)

pause
exit /b %RET%