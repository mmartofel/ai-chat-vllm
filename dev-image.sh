#!/bin/bash
# Start the local text-to-image generation service (SDXL-Turbo).
#
# Run this in a separate terminal alongside dev.sh.
# First start downloads ~5 GB of model weights from HuggingFace.
#
# The main app connects via IMAGE_SERVICE_URL=http://localhost:8100 (default in .env).

cd "$(dirname "$0")/image_service"

python3.12 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

uvicorn main:app --host 0.0.0.0 --port 8100
