import os
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load env vars if present
load_dotenv()

# Configuration
API_BASE_URL = os.getenv("LLM_API_URL", "http://localhost:11434/v1")
API_KEY = os.getenv("LLM_API_KEY", "ollama")
MODEL_NAME = os.getenv("LLM_MODEL", "mistral")

app = FastAPI()

# Initialize OpenAI Client
client = AsyncOpenAI(base_url=API_BASE_URL, api_key=API_KEY)

class ChatRequest(BaseModel):
    messages: list
    model: str = MODEL_NAME

async def stream_generator(messages, model):
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"\n\n[Error: {str(e)}]"

# --- API Routes ---

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    return StreamingResponse(
        stream_generator(request.messages, request.model), 
        media_type="text/plain"
    )

@app.get("/health")
async def health_check():
    return {"status": "ok", "target": API_BASE_URL, "model": MODEL_NAME}

# --- Frontend Serving ---

# 1. Mount the static directory (useful if you add .css or .js files later)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# 2. Serve index.html at the root URL
@app.get("/")
async def read_root():
    return FileResponse('static/index.html')
