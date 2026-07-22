@echo off
REM Launch the tray app without a console window.
cd /d "%~dp0"
start "" pythonw app.py
