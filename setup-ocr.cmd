@echo off
setlocal
cd /d "%~dp0"
if not exist "BE\.venv\Scripts\python.exe" (
    echo Chua co BE\.venv. Hay cai moi truong backend truoc.
    exit /b 1
)
"BE\.venv\Scripts\python.exe" "BE\scripts\setup_ocr.py" %*
exit /b %errorlevel%
