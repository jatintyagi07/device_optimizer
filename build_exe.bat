@echo off
REM Build script for Memory Optimizer Pro
REM Run this ON WINDOWS (PyInstaller cannot cross-compile from Linux/Mac).
REM
REM Usage:
REM   1. Open a Command Prompt in this folder
REM   2. Run:  build_exe.bat
REM   3. Find the finished app at:  dist\MemoryOptimizerPro.exe

echo Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo Building executable...
pyinstaller --noconfirm --onefile --windowed ^
    --name "MemoryOptimizerPro" ^
    --icon "app_icon.ico" ^
    memory_optimizer.py

echo.
echo Done! Your app is at dist\MemoryOptimizerPro.exe
pause
