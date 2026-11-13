@echo off
REM The weekly ritual, one click. Read-only: prints provider health, the
REM evidence gate, and the new-signal sample sizes against the promotion policy.
setlocal
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\weekly_review.py
pause
