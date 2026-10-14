@echo off
REM ===================================================================
REM  Malai Pioneer - one-click launcher for the API and the web server.
REM
REM  Starts both, waits until the API is actually healthy, shows you the
REM  URLs, and stops both again when you press a key.
REM
REM  Double-click this file. No terminals required.
REM ===================================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "APIDIR=%ROOT%apps\api"
set "WEBDIR=%ROOT%apps\web"

REM Interpreter selection state - filled in by :try_python, never hardcoded.
set "PYEXE="
set "PYARGS="
set "PYDESC="
set "PYVER="
set "PYTRIED="
set "PYBEST="
set "PYBESTARG="

echo.
echo  ==========================================================
echo    Malai Pioneer  -  local launcher
echo  ==========================================================
echo.

REM ---------------------------------------------------------------
REM 1. Preflight: folders
REM ---------------------------------------------------------------
for %%D in ("%APIDIR%" "%WEBDIR%") do (
    if not exist "%%~D" (
        echo  [X] Missing folder: %%~D
        echo      Run this script from inside the repository.
        echo.
        pause
        exit /b 1
    )
)
echo  [1/4] Found apps\api and apps\web

REM ---------------------------------------------------------------
REM 2. Preflight: Python
REM    No interpreter path is hardcoded. Candidates are probed IN
REM    ORDER and the first one that can actually import the API's
REM    dependencies wins:
REM      1. apps\api\.venv\Scripts\python.exe  (a project venv always wins)
REM      2. py -3.14  then  py -3                 (the Windows launcher)
REM      3. python                              (whatever is on PATH)
REM      4. %LOCALAPPDATA%\Programs\Python\Python3*\python.exe,
REM         highest passing version wins
REM    Existence is NEVER enough to pass. Bare "python" on PATH is
REM    3.13 here and has neither uvicorn nor chromadb, so every
REM    candidate must survive:  -c "import uvicorn, chromadb"
REM ---------------------------------------------------------------

REM (1) project virtualenv
if exist "%APIDIR%\.venv\Scripts\python.exe" (
    set "PYBEST=%APIDIR%\.venv\Scripts\python.exe"
    call :try_python "%APIDIR%\.venv\Scripts\python.exe" ""
)
if defined PYEXE goto :py_ok

REM (2) the Windows launcher - keeps us off any absolute path
set "PYHASPY="
where py >nul 2>&1
if not errorlevel 1 set "PYHASPY=1"
if not defined PYHASPY set "PYTRIED=%PYTRIED%- py : launcher not installed; "
if defined PYHASPY (
    if not defined PYBEST (
        set "PYBEST=py"
        set "PYBESTARG=-3.14"
    )
    call :try_python "py" "-3.14"
)
if defined PYEXE goto :py_ok
if defined PYHASPY call :try_python "py" "-3"
if defined PYEXE goto :py_ok

REM (3) bare "python" on PATH
set "PYHASPYTHON="
where python >nul 2>&1
if not errorlevel 1 set "PYHASPYTHON=1"
if not defined PYHASPYTHON set "PYTRIED=%PYTRIED%- python : not on PATH; "
if defined PYHASPYTHON (
    if not defined PYBEST set "PYBEST=python"
    call :try_python "python" ""
)
if defined PYEXE goto :py_ok

REM (4) per-user installs; each is probed and the newest one that
REM     passes supersedes the older ones, so the highest version wins.
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~fd\python.exe" (
        if not defined PYBEST set "PYBEST=%%~fd\python.exe"
        call :try_python "%%~fd\python.exe" ""
    )
)
if defined PYEXE goto :py_ok

:py_missing
echo.
echo  [X] No usable Python interpreter was found.
echo.
echo  A candidate only counts if this succeeds:
echo      -c "import uvicorn, chromadb"
echo.
echo  Tried, in order:
echo      %PYTRIED%
echo.
if defined PYBEST (
    echo  Fastest fix - install the API dependencies into that interpreter:
    echo      %PYBEST% %PYBESTARG% -m pip install -r apps\api\requirements.txt
) else (
    echo  Install Python 3.14 first, then run this script again:
    echo      py -3.14 -m pip install -r apps\api\requirements.txt
)
echo.
echo  Or, better, a project virtualenv - it always wins this check:
echo      py -3.14 -m venv apps\api\.venv
echo      apps\api\.venv\Scripts\python -m pip install -r apps\api\requirements.txt
echo.
pause
exit /b 1

:py_ok
REM Say exactly which interpreter was chosen so a 3.13 pick is visible.
REM The doubled leading quote is required: for /f would otherwise
REM swallow the first quote and fail on an executable path.
set "PYVER=unknown"
for /f "delims=" %%v in ('""%PYEXE%" %PYARGS% --version" 2^>nul') do set "PYVER=%%v"
echo  [2/4] Python ready: %PYDESC%  (%PYVER%)
echo         verified: import uvicorn, chromadb OK

REM ---------------------------------------------------------------
REM 3. Preflight: Node / npm
REM ---------------------------------------------------------------
where npm >nul 2>&1
if errorlevel 1 (
    echo  [X] npm was not found on PATH. Install Node.js and try again.
    echo.
    pause
    exit /b 1
)
if not exist "%WEBDIR%\node_modules" (
    echo  [!] apps\web\node_modules is missing - first run may take a while.
)
echo  [3/4] Node and npm ready

REM ---------------------------------------------------------------
REM 4. Free the ports (only whatever is holding them, never all node)
REM ---------------------------------------------------------------
call :free_port 8123
call :free_port 3000
echo  [4/4] Ports 8123 and 3000 are free

echo.
echo  Starting the API (may take 5-10s while Chroma loads) ...
echo  Starting the web server ...
echo.

REM /v:on lets the child print its real exit code when a server dies.
REM %PYEXE% is a single token and %PYARGS% holds any launcher flag
REM (e.g. "-3.14"), so the quoted form below stays a valid command line.
start "Malai - API (port 8123)" /D "%APIDIR%" cmd /v:on /k ""%PYEXE%" %PYARGS% -m uvicorn app.main:app --port 8123 & echo. & echo [API stopped - exit code !ERRORLEVEL!] & echo Press any key to close this window. & pause"
start "Malai - WEB (port 3000)" /D "%WEBDIR%" cmd /v:on /k "npm run dev -- --port 3000 & echo. & echo [WEB stopped - exit code !ERRORLEVEL!] & echo Press any key to close this window. & pause"

REM ---------------------------------------------------------------
REM 5. Wait for the API to answer /health instead of sleeping blindly
REM ---------------------------------------------------------------
set "READY="
for /l %%i in (1,1,45) do (
    if not defined READY (
        powershell -NoProfile -Command "try { if ((Invoke-WebRequest -Uri 'http://127.0.0.1:8123/health' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
        if not errorlevel 1 set "READY=1"
        if not defined READY (
            <nul set /p "=     waiting for the API... %%i/45"
            ping -n 2 127.0.0.1 >nul
            echo.
        )
    )
)

if not defined READY goto :api_failed

echo.
echo  ==========================================================
echo    Both servers are up and healthy.
echo.
echo    Web  ^>  http://localhost:3000
echo    API  ^>  http://localhost:8123/health
echo  ==========================================================
echo.
echo  Logs are in the two windows titled "Malai - API" and "Malai - WEB".
echo  Press any key here to stop both servers.
echo  (Closing this window with the X button will NOT stop them -
echo   press a key instead, or just run run.bat again.)

pause >nul
goto :cleanup

:api_failed
echo.
echo  [X] The API did not come up. Real error:
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8123/health' -UseBasicParsing -TimeoutSec 5).Content } catch { '  ' + $_.Exception.Message }"
echo.
echo  Look in the "Malai - API" window for the full traceback.
echo  Common causes: missing apps\api\.env, or a Chroma/collection error.
echo.
pause >nul

:cleanup
echo.
echo  Stopping the API and the web server ...
call :free_port 8123
call :free_port 3000
echo  Done. You can close this window.
echo.
ping -n 4 127.0.0.1 >nul
endlocal
exit /b 0

REM ===============================================================
REM  try_python <exe> <prefix-args>
REM  Probes one candidate with a REAL capability check. Sets
REM  PYEXE/PYARGS/PYDESC on success, records the failure reason in
REM  PYTRIED otherwise. Never trusts a bare existence check - that is
REM  exactly what would let the dependency-less 3.13 on PATH through.
REM ===============================================================
:try_python
set "TRY_EXE=%~1"
set "TRY_ARG=%~2"
REM Build the label without a trailing space when there are no prefix args.
set "TRY_DESC=%~1"
if not "%~2"=="" set "TRY_DESC=%~1 %~2"
"%TRY_EXE%" %TRY_ARG% -c "import uvicorn, chromadb" >nul 2>&1
set "TRY_RC=%errorlevel%"
if "%TRY_RC%"=="0" goto :try_ok
if "%TRY_RC%"=="1" set "TRY_WHY=no uvicorn and/or chromadb"
if "%TRY_RC%"=="9009" set "TRY_WHY=not found on PATH"
if not defined TRY_WHY set "TRY_WHY=capability check failed (exit %TRY_RC%)"
REM NOTE: never put ">" in the PYTRIED text. cmd re-scans an expanded
REM value for redirection, so an arrow here would silently redirect the
REM echo below and blank the whole failure report.
set "PYTRIED=%PYTRIED%- %TRY_DESC% : %TRY_WHY%; "
goto :eof
:try_ok
set "PYEXE=%TRY_EXE%"
set "PYARGS=%TRY_ARG%"
set "PYDESC=%TRY_DESC%"
goto :eof

REM ===========================================================
REM  free_port <port>
REM  Stops ONLY the process listening on that port, found via
REM  netstat. Never kills node.exe or python.exe by name.
REM ===========================================================
:free_port
set "PORT=%~1"
REM Column 5 of "netstat -ano -p tcp" is the owning PID.
for /f "tokens=2,5" %%a in ('netstat -ano -p tcp ^| findstr "LISTENING" ^| findstr ":%PORT% "') do (
    echo      Port %PORT% was busy - stopping PID %%b ^(%%a^)
    taskkill /PID %%b /T /F >nul 2>&1
    ping -n 2 127.0.0.1 >nul
)
goto :eof