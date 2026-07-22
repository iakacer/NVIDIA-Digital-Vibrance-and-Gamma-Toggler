@echo off
REM Build a single-file, windowed .exe into the "dist" folder.
cd /d "%~dp0"
pip install pyinstaller >nul 2>&1
pyinstaller --noconfirm --onefile --windowed --name "NvColorToggler" ^
    --collect-all customtkinter app.py
echo.
echo Done. See dist\NvColorToggler.exe
pause
