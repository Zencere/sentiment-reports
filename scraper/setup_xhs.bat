@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Xiaohongshu scraper - one-click dependency setup + login
echo ============================================================
echo.

rem --- locate a REAL Python interpreter (not the Store stub) ---
set "PY="
py -3 -c "import sys"    >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys"   >nul 2>nul && set "PY=python"
if not defined PY python3 -c "import sys"  >nul 2>nul && set "PY=python3"

if not defined PY (
    echo.
    echo [ERROR] No working Python interpreter found.
    echo.
    echo Your "python" command is most likely pointing to the Microsoft
    echo Store stub, or your Python install is broken (missing python.exe).
    echo.
    echo To fix it:
    echo   1. Open https://www.python.org/downloads/windows/ in a browser
    echo   2. Download the "Python 3.12" 64-bit Windows installer .exe
    echo   3. Run it and CHECK "Add python.exe to PATH"
    echo   4. Click "Install Now"
    echo   5. Then re-run this setup script
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
echo.

echo [1/4] Upgrading pip ...
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [2/4] Installing requirements (playwright etc.) ...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/4] Self-check: verifying playwright / requests can import ...
%PY% -c "import playwright, requests, bs4, snownlp; print('  OK - dependencies ready')"
if errorlevel 1 goto :fail

echo.
echo [4/4] Opening Xiaohongshu login window. Scan the QR code.
%PY% xhs_login.py

echo.
echo ============================================================
echo  Done. Collect data anytime:
echo     python run_scraper.py -s xiaohongshu -n 3
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] One of the steps above failed. Send me the error message.
echo.
pause
exit /b 1