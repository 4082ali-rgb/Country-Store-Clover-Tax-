@echo off
cd /d "%~dp0"
set /p JNO=Confirm the real next journal number in QBO first. Enter it now (e.g. JJ3148):
if "%JNO%"=="" (
    echo No number entered - nothing changed.
    pause
    exit /b 1
)
python countrystore_je.py --set-journal %JNO%
pause
