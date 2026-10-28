@echo off
rem Start the tool. Prefer the local virtualenv, then fall back to system python.
chcp 65001 >nul
cd /d "%~dp0"

set "PYW=.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto run

where pythonw.exe >nul 2>nul
if errorlevel 1 goto nopython
set "PYW=pythonw.exe"

:run
start "" "%PYW%" main.py
exit /b 0

:nopython
echo Python not found. Please install Python 3.9+ and check "Add python.exe to PATH".
pause
exit /b 1
