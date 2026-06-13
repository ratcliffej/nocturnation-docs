@echo off
REM NocturNation Art-Net to Enttec Pro shim - Windows wrapper.
REM
REM First run: installs pyserial + rich into a local venv under tools\.venv.
REM Subsequent runs: just activates the venv and starts the shim.
REM Forwards all CLI args to the shim.
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

REM Detect a stale venv (Python upgrade can leave the venv pointing
REM at a python.exe that no longer exists). Rebuild if missing.
if exist "%VENV%" if not exist "%VENV%\Scripts\python.exe" (
    echo Existing tools\.venv is stale ^(its Python is gone^); rebuilding.
    rmdir /s /q "%VENV%"
)

if not exist "%VENV%" (
    echo Creating local venv at tools\.venv ^(one-off, ~10 seconds^).
    python -m venv "%VENV%"
    "%VENV%\Scripts\pip.exe" install --quiet --upgrade pip
    "%VENV%\Scripts\pip.exe" install --quiet -r "%HERE%requirements.txt"
    echo Venv ready.
)

"%VENV%\Scripts\python.exe" "%HERE%artnet-to-enttec-pro.py" %*
