@echo off
setlocal
cd /d "%~dp0"

echo Updating There Inspector...
echo.

REM Protect local/generated data before Git changes the working tree.
set "SAFE=%TEMP%\ThereInspector_update_backup"
if exist "%SAFE%" rmdir /s /q "%SAFE%"
mkdir "%SAFE%" >nul 2>&1

if exist "cache" robocopy "cache" "%SAFE%\cache" /E /COPY:DAT /R:1 /W:1 >nul
if exist "database" robocopy "database" "%SAFE%\database" /E /COPY:DAT /R:1 /W:1 >nul
if exist "reports" robocopy "reports" "%SAFE%\reports" /E /COPY:DAT /R:1 /W:1 >nul
if exist "logs" robocopy "logs" "%SAFE%\logs" /E /COPY:DAT /R:1 /W:1 >nul

REM Pull only. Never clean, reset, or delete the working tree.
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Update failed. No cleanup or reset was performed.
    echo Your local cache/database backup remains at:
    echo %SAFE%
    pause
    exit /b 1
)

REM Restore/protect generated local data. /E copies files back but never purges extras.
if exist "%SAFE%\cache" robocopy "%SAFE%\cache" "cache" /E /COPY:DAT /R:1 /W:1 >nul
if exist "%SAFE%\database" robocopy "%SAFE%\database" "database" /E /COPY:DAT /R:1 /W:1 >nul
if exist "%SAFE%\reports" robocopy "%SAFE%\reports" "reports" /E /COPY:DAT /R:1 /W:1 >nul
if exist "%SAFE%\logs" robocopy "%SAFE%\logs" "logs" /E /COPY:DAT /R:1 /W:1 >nul

rmdir /s /q "%SAFE%" >nul 2>&1

echo.
echo Update complete. Local cache, renders, database, reports, and logs were preserved.
echo Starting There Inspector...
python there_inspector_gui.py
