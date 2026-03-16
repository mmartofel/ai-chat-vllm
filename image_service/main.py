import asyncio
import contextlib
import io
import logging
import os
import warnings

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
import torch
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*torch_dtype.*")
from diffusers import AutoPipelineForText2Image


class _HealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_HealthFilter())

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Local Image Generation Service")

MODEL_ID = os.getenv("IMAGE_GEN_MODEL", "stabilityai/sdxl-turbo")

logging.info(f"Using model: {MODEL_ID}")

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

logging.info(f"Using device: {device.upper()} with dtype: {dtype}")

logging.info(f"Loading {MODEL_ID} on {device.upper()} with {dtype}...")
_devnull = os.open(os.devnull, os.O_WRONLY)
_saved_stderr = os.dup(2)
os.dup2(_devnull, 2)
try:
    pipe = AutoPipelineForText2Image.from_pretrained(
        MODEL_ID, torch_dtype=dtype
    ).to(device)
finally:
    os.dup2(_saved_stderr, 2)
    os.close(_saved_stderr)
    os.close(_devnull)
logging.info("Model loaded.")

pipe.set_progress_bar_config(disable=True)

class GenerateRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 576
    steps: int = 6

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}

@app.post("/generate")
async def generate(req: GenerateRequest):
    def _run():
        return pipe(
            req.prompt,
            num_inference_steps=req.steps,
            guidance_scale=0.0,   # required for SDXL-Turbo
            width=req.width,
            height=req.height,
        ).images[0]

    image = await asyncio.to_thread(_run)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
