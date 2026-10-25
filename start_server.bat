@echo off
rem ===========================================================================
rem  MUEStrategy mobile relay server (read-only). Listens on 127.0.0.1 only;
rem  the phone reaches it through "tailscale serve" (see README.md).
rem  NOTE: keep this file ASCII-only (cmd.exe reads .bat in cp932).
rem ===========================================================================
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m server
pause
