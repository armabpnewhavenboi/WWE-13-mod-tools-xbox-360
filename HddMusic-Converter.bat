@echo off
rem Double-click to open the music converter window (needs Python 3.8+ from python.org
rem and ffmpeg.exe + ffprobe.exe in this folder or on your PATH).
rem You can also run commands, e.g.:  HddMusic-Converter.bat convert "D:\Music" -o E:\
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PATH=%~dp0;%PATH%"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m x360music %*
) else (
    python -m x360music %*
)
if errorlevel 1 pause
