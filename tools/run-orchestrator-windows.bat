@echo off
REM NocturNation now-playing orchestrator - Windows wrapper.
REM
REM First run: installs pyserial + rich + winsdk into a local venv under
REM tools\.venv (shared with the Art-Net shim wrapper, run-windows.bat,
REM with winsdk added for SMTC now-playing access).
REM Subsequent runs: activates the venv and starts the orchestrator.
REM Forwards all CLI args to the orchestrator.
REM
REM Requires Python 3.9+ on PATH. Install from python.org if absent (tick
REM "Add Python to PATH" during installer).

setlocal

set "HERE=%~dp0"
set "VENV=%HERE%.venv"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3 not found on PATH. Install from https://www.python.org/downloads/windows/ 1>&2
    exit /b 1
)

if not exist "%VENV%" (
    echo First run: creating local venv at tools\.venv ^(one-off, ~15 seconds^).
    python -m venv "%VENV%"
    "%VENV%\Scripts\pip.exe" install --quiet --upgrade pip
    "%VENV%\Scripts\pip.exe" install --quiet -r "%HERE%requirements.txt"
    "%VENV%\Scripts\pip.exe" install --quiet winsdk
    echo Venv ready.
)

"%VENV%\Scripts\python.exe" "%HERE%nowplaying-orchestrator.py" %*
