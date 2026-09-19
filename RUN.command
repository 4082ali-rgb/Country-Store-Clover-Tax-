#!/bin/bash
cd "$(dirname "$0")"
python3 countrystore_je.py --inbox
read -p "Press Enter to close..."
