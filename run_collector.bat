@echo off
REM Overnight collector. Double-click to run, or start from any shell.
REM
REM Restarts automatically if the process dies. The collector already handles a
REM failing stage or provider internally, so a full exit means something
REM unexpected happened -- and losing the rest of the night's capture to it
REM would be worse than restarting.
REM
REM Paper only. This process cannot sign or broadcast a transaction.

setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "LOGFILE=logs\collector.log"

echo Collector starting. Log: %LOGFILE%
echo Press Ctrl+C twice to stop.
echo.

:loop
echo [%date% %time%] --- collector starting --- >> "%LOGFILE%"
.venv\Scripts\python.exe -m meme_flight_recorder.cli collect --interval 300 --limit 20 >> "%LOGFILE%" 2>&1
echo [%date% %time%] --- exited with code %ERRORLEVEL%, restarting in 60s --- >> "%LOGFILE%"
timeout /t 60 /nobreak >nul
goto loop
