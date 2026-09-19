#!/bin/bash
cd "$(dirname "$0")"
echo "Setting up Country Store JE builder..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required but was not found. Install it from python.org first."
    read -p "Press Enter to close..."
    exit 1
fi

python3 -m pip install --user -r requirements.txt
if [ $? -ne 0 ]; then
    echo "pip install failed - see errors above."
    read -p "Press Enter to close..."
    exit 1
fi

echo ""
echo "Checking for optional OCR support (only needed if a Clover report ever"
echo "turns out to be an image-only 'Print to PDF' with no text layer)..."
if python3 -c "import sys; sys.exit(0 if sys.version_info[:2] < (3, 13) else 1)" 2>/dev/null; then
    if python3 -m pip install --user -r requirements-ocr.txt >/dev/null 2>&1; then
        echo "OCR support installed."
    else
        echo "Skipping - could not install OCR support. Text-based Clover PDFs"
        echo "still work fine without it."
    fi
else
    echo "Skipping - OCR support does not yet support this Python version."
    echo "This is fine: text-based Clover PDFs work without it. If a report"
    echo "ever needs OCR, install Python 3.11 or 3.12 and run SETUP again,"
    echo "or ask for the report to be re-exported as a normal PDF/CSV instead."
fi

mkdir -p inbox output

echo ""
echo "Setup complete. Put today's two Clover reports in inbox/ and run RUN.command,"
echo "or set the starting journal number first with SET JOURNAL NUMBER.command."
read -p "Press Enter to close..."
