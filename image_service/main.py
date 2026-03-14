import asyncio
import io
import logging
import os

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
import torch
from diffusers import AutoPipelineForText2Image


class _HealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_HealthFilter())

app = FastAPI(title="Local Image Generation Service")

MODEL_ID = os.getenv("IMAGE_GEN_MODEL", "stabilityai/sdxl-turbo")

logging.info(f"Using model: {MODEL_ID}")

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

logging.info(f"Using device: {device.upper()} with dtype: {dtype}")

logging.info(f"Loading {MODEL_ID} on {device.upper()} with {dtype}...")
pipe = AutoPipelineForText2Image.from_pretrained(
    MODEL_ID, torch_dtype=dtype
).to(device)

pipe.set_progress_bar_config(disable=True)

class GenerateRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 356
    steps: int = 4

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
