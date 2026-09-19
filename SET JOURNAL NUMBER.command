#!/bin/bash
cd "$(dirname "$0")"
read -p "Confirm the real next journal number in QBO first. Enter it now (e.g. JJ3148): " JNO
if [ -z "$JNO" ]; then
    echo "No number entered - nothing changed."
    read -p "Press Enter to close..."
    exit 1
fi
python3 countrystore_je.py --set-journal "$JNO"
read -p "Press Enter to close..."
