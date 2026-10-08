@echo off
rem Double-click to open the resigner window (needs Python 3.8+ from python.org).
rem You can also drag commands here, e.g.:  WWE13-Resigner.bat verify "D:\mods"
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m x360resign %*
) else (
    python -m x360resign %*
)
if errorlevel 1 pause
