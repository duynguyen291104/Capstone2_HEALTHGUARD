@echo off
cd /d "%~dp0"
if not exist "BE\.venv\Scripts\python.exe" (
  echo Khong tim thay BE\.venv\Scripts\python.exe. Hay cai backend va tao virtual environment truoc.
  pause
  exit /b 1
)
BE\.venv\Scripts\python.exe run_local.py %*
set "HEALTHGUARD_EXIT_CODE=%ERRORLEVEL%"
if not "%HEALTHGUARD_EXIT_CODE%"=="0" pause
exit /b %HEALTHGUARD_EXIT_CODE%
