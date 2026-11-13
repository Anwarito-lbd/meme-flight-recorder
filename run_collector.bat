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
REM --position-usd 10  the operator's 2026-08-15 sizing decision, chosen on cost:
REM                   the round trip is 1.81% of a $10 position against 4.65% of
REM                   $1.00 (the 2%-of-equity default this used to fall back to).
REM                   Leaving it unset accumulated the forward record in the cost
REM                   regime the $10 decision exists to escape.
REM --max-open 5      a secondary backstop only. The binding limits live in
REM                   config: [risk] maximum_open_positions = 2, and
REM                   [risk.micro] maximum_aggregate_open_position_pct = 50, which
REM                   is $25 of a $50 account. Expect the book to hold 2, not 5.
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
  --alert ^
  --position-usd 10.0 ^
  --max-open 5 ^
  --limit 20 ^
  --cycles 1 >> "%LOGFILE%" 2>&1
echo [%date% %time%] --- collector exited with code %ERRORLEVEL% --- >> "%LOGFILE%"

REM One bounded collect cycle, then one scout pass, forever. Sequential on
REM purpose: the scout and the collector draw on the same provider quotas, and
REM concurrent loops re-created the 429 circuit-breaker incident. The scout is
REM the operator's mandate engine -- confluence verdicts and the alert stream --
REM and it had produced only 44 journalled evaluations against the collector's
REM 60,238 observations because nothing ever ran it unattended.
echo [%date% %time%] --- scout starting --- >> "%LOGFILE%"
.venv\Scripts\python.exe -m meme_flight_recorder.cli scout ^
  --level level_2 ^
  --alert ^
  --limit 40 >> "%LOGFILE%" 2>&1
echo [%date% %time%] --- scout exited with code %ERRORLEVEL% --- >> "%LOGFILE%"

REM One bounded media pass: a rotating public subreddit, curated t.me previews,
REM Google News RSS. Discovery only -- mentions are journalled and later ranked
REM by score-sources; nothing here can gate or trade.
echo [%date% %time%] --- media scan starting --- >> "%LOGFILE%"
.venv\Scripts\python.exe -m meme_flight_recorder.cli media-scan >> "%LOGFILE%" 2>&1
echo [%date% %time%] --- media scan exited with code %ERRORLEVEL%, restarting in 60s --- >> "%LOGFILE%"
timeout /t 60 /nobreak >nul
goto loop
