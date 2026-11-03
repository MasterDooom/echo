@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run_echo.pyw"
) else (
    echo Create the virtual environment first. See README.md.
    pause
)
