#!/bin/bash
# Start the image service for text-to-image generation (uses torch/diffusers SDXL-Turbo).
# First start downloads ~5 GB of model weights to ~/.cache/huggingface/.
# Uses python3.12 — torch has no wheel for 3.13+ yet.
# Make sure MinIO is running before starting this service (see podman/minio.sh).

set -e

# Create a dedicated venv inside image_service/ to isolate heavy ML deps from the main app
cd image_service

python3.12 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

uvicorn main:app \
  --host 0.0.0.0 \
  --port 8100 \
  --reload
