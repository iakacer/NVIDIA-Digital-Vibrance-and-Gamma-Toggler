@echo off
REM Build a single-file, windowed .exe into the "dist" folder.
cd /d "%~dp0"
pip install "pyinstaller>=6.22.2" >nul 2>&1
pyinstaller --noconfirm --onefile --windowed --name "NvColorToggler" ^
    --icon "icon.ico" ^
    --add-data "icon.png;." ^
    --add-data "icon.ico;." ^
    --collect-all customtkinter app.py
echo.
echo Done. See dist\NvColorToggler.exe
pause
