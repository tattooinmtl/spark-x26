@echo off
setlocal EnableExtensions
title Spark Code 2.6 - install

rem ===========================================================================
rem  Set this machine up for Spark Code 2.6.
rem
rem  Run it once, from inside the cloned repo:
rem
rem      install.cmd
rem
rem  It checks the git checkout, fetches the llama.cpp CPU build, finds the
rem  model (or downloads it from Hugging Face), puts the `spark` command on
rem  your PATH and runs the self-test.
rem ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "INSTALLER=%ROOT%\harness\install.py"
if not exist "%INSTALLER%" (
    echo [ERROR] installer not found: "%INSTALLER%"
    exit /b 1
)

set "PY="
for %%P in ("py -3.12" "py -3.14" "py -3" "python") do (
    if not defined PY (
        %%~P -c "import sys" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    echo [ERROR] no python interpreter found on PATH
    exit /b 1
)

%PY% "%INSTALLER%" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [install] finished with code %RC%
if not defined NOPAUSE pause
exit /b %RC%
