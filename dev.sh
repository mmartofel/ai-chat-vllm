#!/bin/bash
# Update environment variables as needed to reflect your model server configuration
# LLM API configuration, appropriate defaults are also set in app.py
export LLM_API_URL="http://localhost:11434/v1"
export LLM_API_KEY="ollama"
export LLM_MODEL="mistral"

# initiate virtual environment 
python3 -m venv .venv
source .venv/bin/activate

# upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# python3 main.py

uvicorn app:app \
  --host 0.0.0.0 \
  --port 8001 \
  --reload
