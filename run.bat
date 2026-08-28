@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Auto Live2D - launcher
echo ============================================

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto :missing_venv

echo.
echo [run] Starting Auto Live2D...
"%PYTHON%" main.py
if errorlevel 1 goto :run_err
goto :eof

:run_err
echo.
echo [error] Auto Live2D stopped. Check the message above.
echo         For OBS streaming, do not run python -m http.server instead of this launcher.
pause
goto :eof

:missing_venv
echo.
echo [error] The project virtual environment is missing.
echo         Run setup_windows.ps1 once, then launch run.bat again.
pause
