import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
import bcrypt
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from openai import AsyncOpenAI
from pydantic import BaseModel
from dotenv import load_dotenv

# Load env vars if present
load_dotenv()

# Configuration
API_BASE_URL = os.getenv("LLM_API_URL", "http://localhost:11434/v1")
API_KEY = os.getenv("LLM_API_KEY", "ollama")
MODEL_NAME = os.getenv("LLM_MODEL", "mistral")
DB_URL = os.getenv("DB_URL", "postgresql://admin:admin@localhost:5432/aichat")
JWT_SECRET = os.getenv("JWT_SECRET", "changeme-insecure-default-secret")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

logger = logging.getLogger("uvicorn.app")
logger.warning(f"LLM_NAME = {MODEL_NAME}")
logger.warning(f"LLM_API_URL = {API_BASE_URL}")
logger.warning(f"LLM_API_KEY = {API_KEY}")

# Initialize OpenAI Client
client = AsyncOpenAI(base_url=API_BASE_URL, api_key=API_KEY)

# --- DB helpers ---

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""

async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_TABLE)
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        if count == 0:
            pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES ($1, $2)",
                "admin", pw_hash
            )
            logger.warning("SECURITY: Default admin/admin user created. Change the password immediately!")

# --- Auth helpers ---

def create_jwt(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(access_token: str | None = Cookie(default=None)) -> str:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = decode_jwt(access_token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return username

# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(DB_URL)
    app.state.db = pool
    await init_db(pool)
    yield
    await pool.close()

app = FastAPI(lifespan=lifespan)

# --- Models ---

class ChatRequest(BaseModel):
    messages: list
    model: str = MODEL_NAME

class LoginRequest(BaseModel):
    username: str
    password: str

# --- Streaming ---

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

# --- Auth routes ---

@app.post("/auth/login")
async def login(body: LoginRequest, response: Response, request: Request):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE username=$1 AND is_active=TRUE",
            body.username
        )
    if not row or not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_jwt(body.username)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        max_age=JWT_EXPIRE_HOURS * 3600,
    )
    return {"username": body.username}

@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", samesite="strict")
    return {"ok": True}

@app.get("/auth/me")
async def me(username: str = Depends(get_current_user)):
    return {"username": username}

# --- API routes ---

@app.post("/chat")
async def chat_endpoint(request: ChatRequest, username: str = Depends(get_current_user)):
    return StreamingResponse(
        stream_generator(request.messages, request.model),
        media_type="text/plain"
    )

@app.get("/health")
async def health_check():
    return {"status": "ok", "target": API_BASE_URL, "model": MODEL_NAME}

# --- Frontend Serving ---

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def read_root():
    return FileResponse('static/index.html')
