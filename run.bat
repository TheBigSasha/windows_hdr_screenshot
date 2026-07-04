@echo off
REM Launch HDR Shot from source. Creates a venv + installs on first run.
REM Hardened per issue #15: checks the Python version, only marks the install
REM complete after pip actually succeeds, and checks every exit code.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.hdrshot-install-ok"

REM Prefer the py launcher (avoids the Microsoft Store python stub).
where py >nul 2>nul && (set "LAUNCH=py -3") || (set "LAUNCH=python")

if exist "%PY%" if exist "%MARKER%" goto run

echo First run: setting up HDR Shot...

%LAUNCH% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.10 or newer is required.
    echo Install it from https://www.python.org/downloads/ and run this again.
    exit /b 1
)

if not exist "%PY%" (
    %LAUNCH% -m venv "%VENV%"
    if errorlevel 1 ( echo ERROR: failed to create virtual environment. & exit /b 1 )
)

"%PY%" -m pip install --upgrade pip
if errorlevel 1 ( echo ERROR: failed to upgrade pip. & exit /b 1 )

"%PY%" -m pip install -e ".[gui]"
if errorlevel 1 (
    echo ERROR: dependency installation failed ^(network?^). Re-run to retry.
    exit /b 1
)

REM Only now is the install known-good.
> "%MARKER%" echo ok

:run
"%PY%" -m hdrshot %*
endlocal
