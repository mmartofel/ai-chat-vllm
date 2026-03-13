import io, os
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
import torch
from diffusers import AutoPipelineForText2Image

app = FastAPI(title="Local Image Generation Service")

MODEL_ID = os.getenv("IMAGE_GEN_MODEL", "stabilityai/sdxl-turbo")

print(f"Loading {MODEL_ID} on CPU — first run downloads ~5 GB, subsequent starts are fast...")
pipe = AutoPipelineForText2Image.from_pretrained(
    MODEL_ID, torch_dtype=torch.float32  # float32 required for CPU
)
pipe.set_progress_bar_config(disable=True)


class GenerateRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512
    steps: int = 4


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.post("/generate")
def generate(req: GenerateRequest):
    image = pipe(
        req.prompt,
        num_inference_steps=req.steps,
        guidance_scale=0.0,   # required for SDXL-Turbo
        width=req.width,
        height=req.height,
    ).images[0]
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
