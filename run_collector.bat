@echo off
REM Real-chain SHADOW/PAPER collection with automatic restart.
REM
REM Restarts automatically if the process dies. The collector already handles a
REM failing stage or provider internally, so a full exit means something
REM unexpected happened -- and losing the rest of the run to it would be worse
REM than restarting.
REM
REM PAPER ONLY. This process holds no key and cannot sign or broadcast a
REM transaction; tests/test_no_live_execution.py enforces that.
REM
REM --source both     launchpad newborns AND established movers. The deep-pool
REM                   population only exists in the movers feed; the launchpad
REM                   feed has a median pool of $26.
REM --paper-trade     open and manage paper positions on candidates that clear
REM                   every gate. Without it the collector observes only, which
REM                   is what left the journal holding 40,000 observations and
REM                   zero positions.
REM
REM Do not lower --delay. Pacing is derived from the provider's published rate
REM limit; halving it exhausted Jupiter's quota and every candidate then failed
REM closed on unknown route data.

setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "LOGFILE=logs\collector.log"

echo Collector starting. Log: %LOGFILE%
echo PAPER ONLY - no key is loaded and nothing can be signed.
echo Press Ctrl+C twice to stop.
echo.

:loop
echo [%date% %time%] --- collector starting --- >> "%LOGFILE%"
.venv\Scripts\python.exe -m meme_flight_recorder.cli collect ^
  --source both ^
  --paper-trade ^
  --interval 300 ^
  --limit 20 >> "%LOGFILE%" 2>&1
echo [%date% %time%] --- exited with code %ERRORLEVEL%, restarting in 60s --- >> "%LOGFILE%"
timeout /t 60 /nobreak >nul
goto loop
