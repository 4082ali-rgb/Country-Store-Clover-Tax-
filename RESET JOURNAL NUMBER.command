#!/bin/bash
cd "$(dirname "$0")"
python3 countrystore_je.py --reset-journal
read -p "Press Enter to close..."
