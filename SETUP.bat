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
echo Installing optional OCR support (only needed if a Clover report ever
echo turns out to be an image-only "Print to PDF" with no text layer)...
python -m pip install --user -r requirements-ocr.txt
if errorlevel 1 (
    echo OCR support could not be installed for this Python version - that's fine,
    echo text-based Clover PDFs will still work. Only raster/image-only PDFs need OCR.
)

if not exist inbox mkdir inbox
if not exist output mkdir output

echo.
echo Setup complete. Put today's two Clover reports in inbox\ and run RUN.bat,
echo or set the starting journal number first with "SET JOURNAL NUMBER.bat".
pause
