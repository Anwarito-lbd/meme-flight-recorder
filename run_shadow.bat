@echo off
REM Shadow/paper collector with auto-restart. Paper only: this process holds no
REM key and cannot sign or broadcast. Registered as a logon task so the system
REM survives reboot without needing anyone to remember to start it.
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" -m meme_flight_recorder.cli collect --paper-trade --source both >> "logs\shadow_run.log" 2>&1
echo [%date% %time%] collector exited, restarting in 30s >> "logs\shadow_run.log"
timeout /t 30 /nobreak > nul
goto loop
