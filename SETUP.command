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

mkdir -p inbox output

echo ""
echo "Setup complete. Put today's two Clover reports in inbox/ and run RUN.command,"
echo "or set the starting journal number first with SET JOURNAL NUMBER.command."
read -p "Press Enter to close..."
