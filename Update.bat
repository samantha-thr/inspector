@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   There Inspector Updater
echo ========================================
echo Working folder: %CD%
echo.

REM This updater NEVER cleans or resets the checkout.
REM Generated data is untracked and is left exactly where it is.

git status --short
echo.
echo Pulling gui-3.0 from GitHub...
git pull --ff-only origin gui-3.0
if errorlevel 1 (
    echo.
    echo UPDATE FAILED.
    echo Nothing was cleaned, reset, or deleted.
    echo If Git reports local changes, close Inspector and resolve those changes first.
    pause
    exit /b 1
)

echo.
echo Update complete.
echo Current revision:
git log -1 --oneline
echo.
echo Starting There Inspector...
python there_inspector_gui.py

echo.
echo There Inspector has closed.
pause
