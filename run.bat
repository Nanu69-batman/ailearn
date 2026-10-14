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
set "PY=C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe"

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
REM 2. Preflight: Python (must be 3.14, not whatever is on PATH)
REM ---------------------------------------------------------------
if exist "%PY%" goto :py_ok

echo      The known-good Python was not at:
echo        %PY%
echo      Searching for Python 3.14 ...
set "PY="
for /f "delims=" %%p in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" 2^>nul') do (
    if not defined PY set "PY=%%p"
)
if not defined PY goto :py_missing

:py_ok
REM Confirm this interpreter actually has the API's dependencies.
REM Plain "python" on PATH is 3.13 here and has neither uvicorn nor chromadb.
"%PY%" -c "import uvicorn, chromadb" >nul 2>&1
if errorlevel 1 (
    echo  [X] This Python cannot run the API - it needs uvicorn and chromadb:
    echo        %PY%
    echo      Install them with:
    echo        "%PY%" -m pip install -r apps\api\requirements.txt
    echo.
    pause
    exit /b 1
)
echo  [2/4] Python ready: %PY%

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
start "Malai - API (port 8123)" /D "%APIDIR%" cmd /v:on /k ""%PY%" -m uvicorn app.main:app --port 8123 & echo. & echo [API stopped - exit code !ERRORLEVEL!] & echo Press any key to close this window. & pause"
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