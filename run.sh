#!/bin/bash

# Set PYTHONPATH to current directory
export PYTHONPATH=$(pwd)

# Activate virtual environment if exists
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Run the bot with API
python3 web/api.py
