@echo off
cd /d "%~dp0"
echo Setting up Country Store JE builder...

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3 is required but was not found. Install it from python.org first.
    pause
    exit /b 1
)

python -m pip install --user -r requirements.txt
if errorlevel 1 (
    echo pip install failed - see errors above.
    pause
    exit /b 1
)

echo.
echo Checking for optional OCR support (only needed if a Clover report ever
echo turns out to be an image-only "Print to PDF" with no text layer)...
python -c "import sys; sys.exit(0 if sys.version_info[:2] < (3, 13) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Skipping - OCR support does not yet support this Python version.
    echo This is fine: text-based Clover PDFs work without it. If a report
    echo ever needs OCR, install Python 3.11 or 3.12 and run SETUP again,
    echo or ask for the report to be re-exported as a normal PDF/CSV instead.
) else (
    python -m pip install --user -r requirements-ocr.txt >nul 2>nul
    if errorlevel 1 (
        echo Skipping - could not install OCR support. Text-based Clover PDFs
        echo still work fine without it.
    ) else (
        echo OCR support installed.
    )
)

if not exist inbox mkdir inbox
if not exist output mkdir output

echo.
echo Setup complete. Put today's two Clover reports in inbox\ and run RUN.bat,
echo or set the starting journal number first with "SET JOURNAL NUMBER.bat".
pause
