@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Spark X2.6 4B - llama.cpp (CPU only)

rem ===========================================================================
rem  Spark X2.6 4B  (XHToken)  -  spark2_5 architecture  -  CPU only launcher
rem  Tuned for: Ryzen 7 5700U (8C/16T), 28.9 GB RAM, no dGPU (Vega 8 iGPU)
rem  llama.cpp:  b11873 prebuilt CPU (default)  +  b11721 source build
rem              both copied under .\llama\
rem
rem    start_spark26.bat                 server -> http://127.0.0.1:8081
rem    start_spark26.bat cli             interactive chat in this window
rem    start_spark26.bat bench           measure real throughput
rem
rem  Tokens may be given in any order:
rem    server | cli | bench              what to run
rem    high | low | none                 thinking budget (default: high)
rem    b11873 | b11721                   which llama.cpp build (default: b11873)
rem
rem  The 4th argument is the project directory. It is where the server runs
rem  and where its built-in tools read and write, so the web UI operates on
rem  your code instead of on the llama.cpp build folder.
rem  Default: .\workspace
rem
rem  Examples:
rem    start_spark26.bat                          server, thinking high
rem    start_spark26.bat server low               server, thinking low (faster)
rem    start_spark26.bat cli none                 chat, no thinking
rem    start_spark26.bat server high b11721       server on the source build
rem    start_spark26.bat server high b11873 C:\Trains   serve one project
rem ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem ------------------------------- model -------------------------------------
set "MODEL=C:\models\Spark-X2.6-4B-Q8_0.gguf"
set "TEMPLATE=%ROOT%\spark26-chat-template.jinja"
set "HOST=127.0.0.1"
set "PORT=8081"

rem ----------------------------- tunables ------------------------------------
set "CTX=32768"
set "THREADS=8"
set "THREADS_BATCH=16"
set "BATCH=2048"
set "UBATCH=512"
set "CTK=f16"
set "CTV=f16"
set "POLL=50"
set "PARALLEL=1"
set "CACHE_RAM=512"
set "LOAD_MODE=mmap"
rem Spark's own published sampling defaults (HF README + GGUF metadata):
rem   temperature 1.0, top_p 0.95, top_k disabled, repetition_penalty 1.0
set "TEMP=1.0"
set "TOP_P=0.95"
set "TOP_K=0"
set "REPEAT=1.0"
set "THINK=high"
set "BUILD=b11873"
set "MODE=server"

rem ------------------------- argument parsing ---------------------------------
rem The arguments are copied into plain variables first, one %-form per line.
rem A tilde-modified reference (%~1) is not expanded when it follows another
rem % expansion on the same line - cmd drops the % and leaves "~1" as literal
rem text. Assigning first keeps %~1..%~4 working and keeps a project path
rem with spaces in one piece.
set "A1=%~1"
set "A2=%~2"
set "A3=%~3"
set "A4=%~4"
if not "%A1%"=="" call :classify "%A1%"
if not "%A2%"=="" call :classify "%A2%"
if not "%A3%"=="" call :classify "%A3%"
if defined BADARG goto :badarg

rem The project directory is where the server runs and where its built-in
rem tools read and write. llama.cpp executes those tools in the server's
rem working directory, so this is what makes the web UI useful on a real
rem codebase instead of on the llama.cpp build folder.
rem Without a 4th argument the project used last is reused, so neither
rem launcher has to be told the same path twice.
set "PROJECT=%ROOT%\workspace"
if exist "%ROOT%\last-project.txt" for /f "usebackq delims=" %%P in ("%ROOT%\last-project.txt") do set "REMEMBERED=%%P"
if defined REMEMBERED set "REMEMBERED=%REMEMBERED:~1,-1%"
if defined REMEMBERED if exist "%REMEMBERED%" set "PROJECT=%REMEMBERED%"
if not "%A4%"=="" set "PROJECT=%A4%"

if /i "%BUILD%"=="b11721" set "LLAMA_DIR=%ROOT%\llama\llama-b11721-notecode"
if /i not "%BUILD%"=="b11721" set "LLAMA_DIR=%ROOT%\llama\llama-b11873-cpu"

rem thinking flags: the Spark template accepts ONLY "high" and "low",
rem and raises a template exception for anything else.
set "THINK_ARGS="
if /i "%THINK%"=="none" (
    set "THINK_ARGS=--reasoning off"
) else if /i "%THINK%"=="low" (
    set "THINK_ARGS=--reasoning on --reasoning-effort low"
) else (
    set "THINK_ARGS=--reasoning on --reasoning-effort high"
)

rem ============================== preflight ===================================
if not exist "%LLAMA_DIR%\llama-server.exe" (
    echo [ERROR] llama.cpp build not found at "%LLAMA_DIR%"
    if not defined NOPAUSE pause
    exit /b 1
)
if not exist "%MODEL%" (
    echo [ERROR] model not found: "%MODEL%"
    if not defined NOPAUSE pause
    exit /b 1
)
if not exist "%TEMPLATE%" (
    echo [ERROR] chat template not found: "%TEMPLATE%"
    if not defined NOPAUSE pause
    exit /b 1
)
if not exist "%PROJECT%" (
    echo [ERROR] project directory not found: "%PROJECT%"
    if not defined NOPAUSE pause
    exit /b 1
)

rem Remember this project for the next run of either launcher.
>"%ROOT%\last-project.txt" echo "%PROJECT%"

set "FREE_GB=0"
for /f %%f in ('powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB)"') do set "FREE_GB=%%f"
set "NEED_GB=6"

echo =======================================================
echo  Spark X2.6 4B  -  spark2_5 arch  -  CPU only
echo =======================================================
echo  llama.cpp   : %BUILD%   %LLAMA_DIR%
echo  Model       : %MODEL%
echo  Template    : %TEMPLATE%
echo  Project     : %PROJECT%
echo  Context     : %CTX%
echo  Threads     : %THREADS% decode / %THREADS_BATCH% prompt  ^(8 cores, 16 SMT^)
echo  KV cache    : %CTK% / %CTV%
echo  Sampling    : temp %TEMP%  top_p %TOP_P%  top_k %TOP_K%  repeat %REPEAT%
echo  Thinking    : %THINK%
echo  Mode        : %MODE%
echo  Free RAM    : !FREE_GB! GB of 28.9 GB  (need ~%NEED_GB% GB)
echo =======================================================
if !FREE_GB! LSS %NEED_GB% (
    echo  [WARN] Only !FREE_GB! GB free. Close memory-heavy apps first.
    echo.
)

rem Run from the project directory: llama.cpp executes its built-in tools
rem (read_file, write_file, exec_shell_command) in the working directory of
rem the server process, so this is what the web UI operates on.
cd /d "%PROJECT%"

rem ================================ dispatch ===================================
if /i "%MODE%"=="bench" goto :bench
if /i "%MODE%"=="cli"   goto :cli
goto :server

rem ------------------------------- server -------------------------------------
:server
echo Starting llama-server on http://%HOST%:%PORT%   (Ctrl+C to stop)
echo.
"%LLAMA_DIR%\llama-server.exe" ^
    -m "%MODEL%" ^
    --host %HOST% ^
    --port %PORT% ^
    --ctx-size %CTX% ^
    --threads %THREADS% ^
    --threads-batch %THREADS_BATCH% ^
    --batch-size %BATCH% ^
    --ubatch-size %UBATCH% ^
    --cache-type-k %CTK% ^
    --cache-type-v %CTV% ^
    --flash-attn on ^
    --poll %POLL% ^
    --parallel %PARALLEL% ^
    --cache-ram %CACHE_RAM% ^
    --load-mode %LOAD_MODE% ^
    --n-gpu-layers 0 ^
    --device none ^
    --jinja ^
    --chat-template-file "%TEMPLATE%" ^
    --reasoning-format deepseek ^
    %THINK_ARGS% ^
    --temp %TEMP% --top-p %TOP_P% --top-k %TOP_K% --repeat-penalty %REPEAT% ^
    --webui-mcp-proxy ^
    --tools read_file,file_glob_search,grep_search,write_file,edit_file,exec_shell_command

echo.
echo llama-server exited with code %ERRORLEVEL%
if not defined NOPAUSE pause
exit /b %ERRORLEVEL%

rem -------------------------------- cli ---------------------------------------
:cli
echo Interactive chat. Type your message, Enter to send, /bye to quit.
echo.
"%LLAMA_DIR%\llama-cli.exe" ^
    -m "%MODEL%" ^
    --chat-template-file "%TEMPLATE%" ^
    --ctx-size %CTX% ^
    --threads %THREADS% ^
    --threads-batch %THREADS_BATCH% ^
    --batch-size %BATCH% ^
    --ubatch-size %UBATCH% ^
    --cache-type-k %CTK% ^
    --cache-type-v %CTV% ^
    --flash-attn on ^
    --poll %POLL% ^
    --load-mode %LOAD_MODE% ^
    --n-gpu-layers 0 ^
    --device none ^
    --jinja ^
    --reasoning-format deepseek ^
    %THINK_ARGS% ^
    --temp %TEMP% --top-p %TOP_P% --top-k %TOP_K% --repeat-penalty %REPEAT%

echo.
echo llama-cli exited with code %ERRORLEVEL%
if not defined NOPAUSE pause
exit /b %ERRORLEVEL%

rem ------------------------------- bench --------------------------------------
:bench
echo Measuring real throughput (prompt and token generation).
echo.
"%LLAMA_DIR%\llama-bench.exe" ^
    -m "%MODEL%" ^
    -t %THREADS% ^
    -p 512 ^
    -n 64 ^
    -r 1

echo.
if not defined NOPAUSE pause
exit /b %ERRORLEVEL%

rem ----------------------------- classifier -----------------------------------
rem A goto out of a called subroutine does not end the script: "exit /b"
rem only leaves the call. The problem is therefore reported through BADARG
rem and handled by the main flow, where "exit /b" really does end the run.
:classify
set "T=%~1"
if "%T%"=="" goto :eof
if /i "%T%"=="server"  set "MODE=server"  & goto :eof
if /i "%T%"=="cli"    set "MODE=cli"    & goto :eof
if /i "%T%"=="bench"  set "MODE=bench"  & goto :eof
if /i "%T%"=="high"   set "THINK=high"  & goto :eof
if /i "%T%"=="low"    set "THINK=low"   & goto :eof
if /i "%T%"=="none"   set "THINK=none"  & goto :eof
if /i "%T%"=="b11873" set "BUILD=b11873" & goto :eof
if /i "%T%"=="b11721" set "BUILD=b11721" & goto :eof
if /i "%T%"=="help"   set "BADARG=help"  & goto :eof
if /i "%T%"=="/?"    set "BADARG=help"  & goto :eof
set "BADARG=%T%"
goto :eof

:badarg
if /i "%BADARG%"=="help" goto :usage
echo [ERROR] unknown argument: %BADARG%
goto :usage

:usage
echo Usage: start_spark26.bat [server^|cli^|bench] [high^|low^|none] [b11873^|b11721] [project]
echo.
echo   server  llama-server with the OpenAI API + web UI (default)
echo   cli     interactive chat in this window
echo   bench   llama-bench throughput measurement
echo   high    thinking effort high (default)   low   faster, shallower
echo   none    no thinking                     b11873 / b11721  build choice
echo   project directory the server runs in; its built-in tools read and
echo   write there, so the web UI works on your code (default .\workspace)
if not defined NOPAUSE pause
exit /b 1
