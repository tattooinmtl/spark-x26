@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Spark X2.6 - Coding Agent (llama.cpp)

rem ===========================================================================
rem  Spark Agent - a coding harness around llama.cpp + Spark X2.6 4B
rem
rem  The agent drives the model with Spark's own chat template, exposes a
rem  tool set (read_file, list_dir, grep, write_file, edit_file, run_command,
rem  git_status, git_diff, git_log, read_skill, finish) and loads skills on
rem  demand. It also carries web_search, web_fetch, the headless browser
rem  tools and every tool the configured MCP servers publish.
rem
rem  Usage:
rem    start_spark26_agent.bat                        interactive session
rem    start_spark26_agent.bat cli                    same, on the llama-cli engine
rem    start_spark26_agent.bat server --task "..."    run one task and exit
rem    start_spark26_agent.bat --workspace C:\work    work in another folder
rem    start_spark26_agent.bat --think low            less thinking, faster turns
rem    start_spark26_agent.bat --no-web --no-mcp      local tools only
rem    start_spark26_agent.bat --render               print the rendered prompt
rem
rem  With no arguments it opens an interactive session: type an instruction,
rem  read the answer, type the next one. Type /help for the command list.
rem ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "HARNESS=%ROOT%\harness\spark_agent.py"
set "WORKSPACE=%ROOT%\workspace"
set "ENGINE=server"
set "THINK=high"

rem --- first positional argument: engine (server^|cli) ----------------------
set "ARGS=%*"
set "FIRST=%~1"
if /i "%FIRST%"=="cli"    set "ENGINE=cli"
if /i "%FIRST%"=="server" set "ENGINE=server"
if /i "%FIRST%"=="cli"    set "ARGS=%2 %3 %4 %5 %6 %7 %8 %9"
if /i "%FIRST%"=="server" set "ARGS=%2 %3 %4 %5 %6 %7 %8 %9"

if not exist "%HARNESS%" (
    echo [ERROR] harness not found: "%HARNESS%"
    if not defined NOPAUSE pause
    exit /b 1
)

rem --- pick a python that exists -------------------------------------------
set "PY="
for %%P in ("py -3.12" "py -3.14" "py -3" "python") do (
    if not defined PY (
        %%~P -c "import sys" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    echo [ERROR] no python interpreter found on PATH
    if not defined NOPAUSE pause
    exit /b 1
)

rem The project the launchers used last. An explicit --workspace in the
rem argument list wins and is remembered by the harness; without one the
rem harness opens the project that was used last.
set "WORKSPACE=%ROOT%\workspace"
if exist "%ROOT%\last-project.txt" (
    for /f "usebackq delims=" %%P in ("%ROOT%\last-project.txt") do set "WORKSPACE=%%P"
)
if defined WORKSPACE set "WORKSPACE=%WORKSPACE:~1,-1%"
if not exist "%WORKSPACE%" set "WORKSPACE=%ROOT%\workspace"

if not exist "%WORKSPACE%" mkdir "%WORKSPACE%"

rem Without an explicit task the agent opens an interactive session, so a
rem double-clicked window never dies with "no task given". A piped task
rem still works: it becomes the first instruction and EOF ends the session.
echo %ARGS% | findstr /c:"--task" >nul 2>&1
if errorlevel 1 set "ARGS=%ARGS% --interactive"

rem The banner must not claim a thinking level that the caller's own
rem --think overrides, so check whether the argument list carries one.
set "THINK_SHOWN=%THINK%"
echo %ARGS% | findstr /c:"--think" >nul 2>&1 && set "THINK_SHOWN=from --think"

echo [ Spark Agent ]  engine: %ENGINE%   workspace: %WORKSPACE%
echo [ Spark Agent ]  thinking: %THINK_SHOWN%   python: %PY%
echo.

rem --workspace is deliberately NOT passed here: without it the harness opens
rem the project that was used last, and with it the caller's own path wins
rem (argparse takes the last occurrence).
%PY% "%HARNESS%" %ENGINE% --think %THINK% %ARGS%

echo.
echo Spark Agent exited with code %ERRORLEVEL%
if not defined NOPAUSE pause
exit /b %ERRORLEVEL%
