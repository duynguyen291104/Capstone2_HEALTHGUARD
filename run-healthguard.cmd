@echo off
cd /d "%~dp0"
BE\.venv\Scripts\python.exe run_local.py
if errorlevel 1 pause
