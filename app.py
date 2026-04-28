import io
import os
import uuid
import json
import base64
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
import bcrypt
import httpx
import pypdf
import docx as _docx
from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from miniopy_async import Minio
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
    FilterSelector,
)

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
VISION_MODEL      = os.getenv("VISION_MODEL",          "moondream")
VISION_BASE_URL   = os.getenv("VISION_BASE_URL",       "http://localhost:11434/v1")
VISION_API_KEY    = os.getenv("VISION_API_KEY",        "ollama")
IMAGE_SERVICE_URL = os.getenv("IMAGE_SERVICE_URL",     "http://localhost:8100")
MINIO_ENDPOINT    = os.getenv("MINIO_ENDPOINT",        "http://localhost:9000")
MINIO_ACCESS_KEY  = os.getenv("MINIO_ACCESS_KEY",      "admin")
MINIO_SECRET_KEY  = os.getenv("MINIO_SECRET_KEY",      "admin123")
MINIO_BUCKET      = os.getenv("MINIO_BUCKET",          "images")
MINIO_PUBLIC_BASE = os.getenv("MINIO_PUBLIC_BASE_URL", "http://localhost:9000/images")
QDRANT_URL        = os.getenv("QDRANT_URL",            "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION",     "documents")
EMBED_MODEL       = os.getenv("EMBED_MODEL",           "nomic-ai/nomic-embed-text-v1.5")
EMBED_BASE_URL    = os.getenv("EMBED_BASE_URL",        "http://localhost:11434/v1")
EMBED_API_KEY     = os.getenv("EMBED_API_KEY",         "dummy-key")
EMBED_DIM         = int(os.getenv("EMBED_DIM",         "768"))

logger = logging.getLogger("uvicorn.app")
logger.warning(f"LLM_NAME = {MODEL_NAME}")
logger.warning(f"LLM_API_URL = {API_BASE_URL}")
logger.warning(f"LLM_API_KEY = {API_KEY}")
logger.warning(f"DB_URL = {DB_URL}")

# Initialize OpenAI Client
client = AsyncOpenAI(base_url=API_BASE_URL, api_key=API_KEY)

# Initialize MinIO client
minio_client = Minio(
    MINIO_ENDPOINT.replace("http://", "").replace("https://", ""),
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_ENDPOINT.startswith("https://"),
)

# RAG clients (initialized at module level; Qdrant collection ensured at startup)
qdrant  = AsyncQdrantClient(url=QDRANT_URL)
embed_client = AsyncOpenAI(base_url=EMBED_BASE_URL, api_key=EMBED_API_KEY)

# --- RBAC ---

ROLE_PERMISSIONS = {
    "admin":     {"chat", "manage_users", "manage_roles", "moderate_content"},
    "moderator": {"chat", "moderate_content"},
    "user":      {"chat"},
}

BUILTIN_ROLES = {"admin", "moderator", "user"}

# --- DB helpers ---

INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS roles (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS permissions (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES roles(id);

CREATE TABLE IF NOT EXISTS conversations (
    id         VARCHAR(32)  PRIMARY KEY,
    user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(255) NOT NULL DEFAULT '',
    messages   JSONB        NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS image_generations (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  VARCHAR(32)  NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id          INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode             VARCHAR(20)  NOT NULL CHECK (mode IN ('image_to_text', 'text_to_image')),
    prompt           TEXT,
    source_image_key TEXT,
    result_image_key TEXT,
    result_text      TEXT,
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_image_gen_conversation ON image_generations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_image_gen_user         ON image_generations(user_id);

CREATE TABLE IF NOT EXISTS documents (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename      TEXT         NOT NULL,
    file_type     VARCHAR(10)  NOT NULL,
    file_key      TEXT         NOT NULL,
    chunk_count   INTEGER      NOT NULL DEFAULT 0,
    status        VARCHAR(20)  NOT NULL DEFAULT 'processing'
                               CHECK (status IN ('processing', 'ready', 'failed')),
    error_message TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
"""


async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(INIT_SQL)

        # Log database connection and seeding info
        logger.info("Connecting to PostgreSQL database to initialize ...")

        # Seed permissions
        for perm in ("chat", "manage_users", "manage_roles", "moderate_content"):
            await conn.execute(
                "INSERT INTO permissions (name) VALUES ($1) ON CONFLICT DO NOTHING", perm
            )

        # Seed roles
        for role_name in ("admin", "moderator", "user"):
            await conn.execute(
                "INSERT INTO roles (name) VALUES ($1) ON CONFLICT DO NOTHING", role_name
            )

        # Seed role_permissions
        role_perm_map = {
            "admin":     ["chat", "manage_users", "manage_roles", "moderate_content"],
            "moderator": ["chat", "moderate_content"],
            "user":      ["chat"],
        }
        for role_name, perms in role_perm_map.items():
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", role_name)
            for perm in perms:
                perm_id = await conn.fetchval("SELECT id FROM permissions WHERE name=$1", perm)
                await conn.execute(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    role_id, perm_id
                )

        # Seed default admin user if table is empty
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        if count == 0:
            pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
            admin_role_id = await conn.fetchval("SELECT id FROM roles WHERE name='admin'")
            await conn.execute(
                "INSERT INTO users (username, password_hash, role_id) VALUES ($1, $2, $3)",
                "admin", pw_hash, admin_role_id
            )
            logger.warning("SECURITY: Default admin/admin user created. Change the password immediately!")

            # Seed test users
            mod_role_id = await conn.fetchval("SELECT id FROM roles WHERE name='moderator'")
            user_role_id = await conn.fetchval("SELECT id FROM roles WHERE name='user'")

            mod_hash = bcrypt.hashpw(b"moderator", bcrypt.gensalt()).decode()
            await conn.execute(
                "INSERT INTO users (username, password_hash, role_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                "moderator_user", mod_hash, mod_role_id
            )
            reg_hash = bcrypt.hashpw(b"user", bcrypt.gensalt()).decode()
            await conn.execute(
                "INSERT INTO users (username, password_hash, role_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                "regular_user", reg_hash, user_role_id
            )
        else:
            # Ensure existing admin user has the admin role set
            admin_role_id = await conn.fetchval("SELECT id FROM roles WHERE name='admin'")
            await conn.execute(
                "UPDATE users SET role_id=$1 WHERE username='admin' AND role_id IS NULL",
                admin_role_id
            )


# --- Auth helpers ---

def create_jwt(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "role": role, "exp": expire}, JWT_SECRET, algorithm="HS256")


def decode_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
        role = payload.get("role", "user")
        if not username:
            return None
        return {"username": username, "role": role}
    except JWTError:
        return None


def get_current_user(access_token: str | None = Cookie(default=None)) -> dict:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = decode_jwt(access_token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


def require_permission(perm: str):
    def dependency(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role", "user")
        if perm not in ROLE_PERMISSIONS.get(role, set()):
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency


# --- Lifespan ---

async def ensure_qdrant_collection():
    try:
        existing = await qdrant.get_collections()
        names = [c.name for c in existing.collections]
        if QDRANT_COLLECTION not in names:
            await qdrant.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )
        logger.info(f"Qdrant init completed, collection '{QDRANT_COLLECTION}' is ready")
    except Exception as e:
        logger.warning(f"Qdrant init skipped (will retry on first use): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(DB_URL)
    app.state.db = pool
    await init_db(pool)
    await ensure_qdrant_collection()
    yield
    await pool.close()

app = FastAPI(lifespan=lifespan)

# --- Models ---

class ChatRequest(BaseModel):
    messages: list
    model: str = MODEL_NAME
    use_rag: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role_name: str = "user"
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    role_name: str | None = None
    is_active: bool | None = None


class CreateRoleRequest(BaseModel):
    name: str
    permissions: list[str] = []


class UpdateRoleRequest(BaseModel):
    name: str | None = None
    permissions: list[str] = []


class ConversationUpsertRequest(BaseModel):
    title: str
    messages: list        # [{role, content, durationMs}]
    created_at: int       # Unix ms from client


class ImageGenerateRequest(BaseModel):
    conversation_id: str
    prompt: str
    width: int = 512
    height: int = 512
    previous_image_url: Optional[str] = None


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    chunk_count: int
    status: str


# --- RAG helpers ---

def extract_text(file_bytes: bytes, file_type: str) -> str:
    if file_type == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if file_type == "docx":
        doc = _docx.Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs)
    return file_bytes.decode("utf-8", errors="replace")


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += size - overlap
    return [c for c in chunks if c.strip()]


CHAR_BUDGET = 12_000  # ~3000 tokens at 4 chars/token, leaving ~1000 tokens for response


def truncate_to_budget(msgs: list, budget: int = CHAR_BUDGET) -> list:
    total = sum(len(str(m.get("content", ""))) for m in msgs)
    start = 1 if msgs and msgs[0].get("role") == "system" else 0
    while total > budget and len(msgs) > start + 1:
        removed = msgs.pop(start)
        total -= len(str(removed.get("content", "")))
    return msgs


async def condense_query(messages: list) -> str:
    if len(messages) <= 1:
        last = messages[-1] if messages else {}
        return str(last.get("content", ""))
    history = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')}"
        for m in messages[-7:-1]
    )
    latest = str(messages[-1].get("content", ""))
    resp = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content":
            f"Conversation so far:\n{history}\n\n"
            f"Rewrite this message as a self-contained search query "
            f"(no explanation, just the query): {latest}"
        }],
        max_tokens=80,
        temperature=0,
    )
    return resp.choices[0].message.content.strip()


async def stream_with_sources(messages, model, sources):
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        if sources:
            yield f"\n%%SOURCES%%{json.dumps(sources)}"
    except Exception as e:
        yield f"\n\n[Error: {e}]"


# --- Streaming ---

# --- Chat route ---

@app.post("/chat")
async def chat_endpoint(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(require_permission("chat")),
):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])

    messages = list(body.messages)
    sources  = []

    try:
        if body.use_rag:
            doc_count = await qdrant.count(
                collection_name=QDRANT_COLLECTION,
                count_filter=Filter(must=[
                    FieldCondition(key="user_id", match=MatchValue(value=uid))
                ]),
            )
            if doc_count.count > 0 and messages:
                condensed  = await condense_query(messages)
                embed_resp = await embed_client.embeddings.create(
                    model=EMBED_MODEL, input=[condensed]
                )
                query_vec  = embed_resp.data[0].embedding
                _rag_resp  = await qdrant.query_points(
                    collection_name=QDRANT_COLLECTION,
                    query=query_vec,
                    query_filter=Filter(must=[
                        FieldCondition(key="user_id", match=MatchValue(value=uid))
                    ]),
                    limit=3,
                    score_threshold=0.3,
                )
                hits = _rag_resp.points
                logger.info(f"RAG: query='{condensed[:80]}' hits={len(hits)} scores={[round(h.score,3) for h in hits]}")
                if hits:
                    context  = "\n\n---\n".join(h.payload["text"][:500] for h in hits)
                    messages = [{
                        "role": "system",
                        "content": (
                            "Answer using the document context below. "
                            "If it is not relevant, use your general knowledge.\n\n"
                            + context
                        ),
                    }] + messages
                    sources = [
                        {"filename": h.payload["filename"],
                         "excerpt":  h.payload["text"][:200]}
                        for h in hits
                    ]
    except Exception as rag_err:
        logger.warning(f"RAG retrieval failed: {rag_err}")  # RAG failure must never break chat

    messages = truncate_to_budget(messages)

    return StreamingResponse(
        stream_with_sources(messages, body.model, sources),
        media_type="text/plain",
    )


# --- Auth routes ---

@app.post("/auth/login")
async def login(body: LoginRequest, response: Response, request: Request):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT u.password_hash, COALESCE(r.name, 'user') AS role
               FROM users u
               LEFT JOIN roles r ON r.id = u.role_id
               WHERE u.username=$1 AND u.is_active=TRUE""",
            body.username
        )
    if not row or not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    role = row["role"]
    token = create_jwt(body.username, role)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        max_age=JWT_EXPIRE_HOURS * 3600,
    )
    return {"username": body.username, "role": role}


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", samesite="strict")
    return {"ok": True}


@app.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "role": user["role"]}


# --- API routes ---

@app.get("/health")
async def health_check():
    return {"status": "ok", "target": API_BASE_URL, "model": MODEL_NAME}


# --- Conversation helpers ---

async def get_user_id(conn, username: str) -> int:
    uid = await conn.fetchval("SELECT id FROM users WHERE username=$1", username)
    if uid is None:
        raise HTTPException(status_code=401, detail="User not found")
    return uid


async def ensure_bucket():
    """Create the images bucket if it does not exist yet, and ensure public read policy."""
    exists = await minio_client.bucket_exists(MINIO_BUCKET)
    if not exists:
        await minio_client.make_bucket(MINIO_BUCKET)
        policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{MINIO_BUCKET}/*"],
            }],
        })
        await minio_client.set_bucket_policy(MINIO_BUCKET, policy)


# --- Conversation routes ---

@app.get("/conversations")
async def list_conversations(request: Request, user: dict = Depends(require_permission("chat"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        rows = await conn.fetch(
            """SELECT id, title, jsonb_array_length(messages) AS message_count,
                      created_at, updated_at
               FROM conversations
               WHERE user_id=$1
               ORDER BY updated_at DESC""",
            uid
        )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "message_count": r["message_count"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, request: Request, user: dict = Depends(require_permission("chat"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        row = await conn.fetchrow(
            "SELECT id, title, messages, created_at, updated_at FROM conversations WHERE id=$1 AND user_id=$2",
            conv_id, uid
        )
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": row["id"],
        "title": row["title"],
        "messages": json.loads(row["messages"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@app.put("/conversations/{conv_id}")
async def upsert_conversation(conv_id: str, body: ConversationUpsertRequest, request: Request, user: dict = Depends(require_permission("chat"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        row = await conn.fetchrow(
            """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
               VALUES ($1, $2, $3, $4::jsonb, to_timestamp($5 / 1000.0), NOW())
               ON CONFLICT (id) DO UPDATE
                 SET title = EXCLUDED.title, messages = EXCLUDED.messages, updated_at = NOW()
               WHERE conversations.user_id = $2
               RETURNING id, updated_at""",
            conv_id, uid, body.title, json.dumps(body.messages), body.created_at
        )
    if not row:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {"id": row["id"], "updated_at": row["updated_at"].isoformat()}


@app.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str, request: Request, user: dict = Depends(require_permission("chat"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        deleted = await conn.fetchval(
            "DELETE FROM conversations WHERE id=$1 AND user_id=$2 RETURNING id",
            conv_id, uid
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")


# --- Admin: Users ---

@app.get("/admin/users")
async def admin_list_users(request: Request, user: dict = Depends(require_permission("manage_users"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.id, u.username, u.is_active, u.created_at,
                      COALESCE(r.name, 'user') AS role_name
               FROM users u
               LEFT JOIN roles r ON r.id = u.role_id
               ORDER BY u.id"""
        )
    return [
        {
            "id": r["id"],
            "username": r["username"],
            "is_active": r["is_active"],
            "created_at": r["created_at"].isoformat(),
            "role_name": r["role_name"],
        }
        for r in rows
    ]


@app.post("/admin/users", status_code=201)
async def admin_create_user(body: CreateUserRequest, request: Request, user: dict = Depends(require_permission("manage_users"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role_name)
        if role_id is None:
            raise HTTPException(status_code=400, detail=f"Role '{body.role_name}' not found")
        pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        try:
            row = await conn.fetchrow(
                "INSERT INTO users (username, password_hash, role_id, is_active) VALUES ($1, $2, $3, $4) RETURNING id",
                body.username, pw_hash, role_id, body.is_active
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="Username already exists")
    return {"id": row["id"], "username": body.username, "role_name": body.role_name}


@app.put("/admin/users/{user_id}")
async def admin_update_user(user_id: int, body: UpdateUserRequest, request: Request, user: dict = Depends(require_permission("manage_users"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT username FROM users WHERE id=$1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["username"] == user["username"]:
            raise HTTPException(status_code=400, detail="Cannot modify your own account")

        if body.role_name is not None:
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role_name)
            if role_id is None:
                raise HTTPException(status_code=400, detail=f"Role '{body.role_name}' not found")
            await conn.execute("UPDATE users SET role_id=$1 WHERE id=$2", role_id, user_id)

        if body.is_active is not None:
            await conn.execute("UPDATE users SET is_active=$1 WHERE id=$2", body.is_active, user_id)

    return {"ok": True}


@app.delete("/admin/users/{user_id}", status_code=204)
async def admin_delete_user(user_id: int, request: Request, user: dict = Depends(require_permission("manage_users"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT username FROM users WHERE id=$1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["username"] == user["username"]:
            raise HTTPException(status_code=400, detail="Cannot delete your own account")
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)


# --- Admin: Roles ---

@app.get("/admin/roles")
async def admin_list_roles(request: Request, user: dict = Depends(require_permission("manage_roles"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.id, r.name,
                      COALESCE(array_agg(p.name) FILTER (WHERE p.name IS NOT NULL), ARRAY[]::TEXT[]) AS permissions
               FROM roles r
               LEFT JOIN role_permissions rp ON rp.role_id = r.id
               LEFT JOIN permissions p ON p.id = rp.permission_id
               GROUP BY r.id, r.name
               ORDER BY r.id"""
        )
    return [
        {"id": r["id"], "name": r["name"], "permissions": list(r["permissions"])}
        for r in rows
    ]


@app.post("/admin/roles", status_code=201)
async def admin_create_role(body: CreateRoleRequest, request: Request, user: dict = Depends(require_permission("manage_roles"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO roles (name) VALUES ($1) RETURNING id", body.name
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="Role name already exists")
        role_id = row["id"]
        for perm in body.permissions:
            perm_id = await conn.fetchval("SELECT id FROM permissions WHERE name=$1", perm)
            if perm_id:
                await conn.execute(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    role_id, perm_id
                )
    return {"id": role_id, "name": body.name, "permissions": body.permissions}


@app.put("/admin/roles/{role_id}")
async def admin_update_role(role_id: int, body: UpdateRoleRequest, request: Request, user: dict = Depends(require_permission("manage_roles"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT name FROM roles WHERE id=$1", role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        if body.name is not None:
            if role["name"] in BUILTIN_ROLES and body.name != role["name"]:
                raise HTTPException(status_code=400, detail="Cannot rename built-in roles")
            try:
                await conn.execute("UPDATE roles SET name=$1 WHERE id=$2", body.name, role_id)
            except asyncpg.UniqueViolationError:
                raise HTTPException(status_code=409, detail="Role name already exists")

        # Replace permissions
        await conn.execute("DELETE FROM role_permissions WHERE role_id=$1", role_id)
        for perm in body.permissions:
            perm_id = await conn.fetchval("SELECT id FROM permissions WHERE name=$1", perm)
            if perm_id:
                await conn.execute(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    role_id, perm_id
                )
    return {"ok": True}


@app.delete("/admin/roles/{role_id}", status_code=204)
async def admin_delete_role(role_id: int, request: Request, user: dict = Depends(require_permission("manage_roles"))):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT name FROM roles WHERE id=$1", role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role["name"] in BUILTIN_ROLES:
            raise HTTPException(status_code=400, detail="Cannot delete built-in roles")
        await conn.execute("DELETE FROM roles WHERE id=$1", role_id)


# --- Image routes ---

@app.post("/images/describe")
async def image_describe(
    request: Request,
    conversation_id: str = Form(...),
    prompt: str = Form(default="Describe this image in detail."),
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("chat")),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    pool: asyncpg.Pool = request.app.state.db
    image_bytes = await file.read()
    image_b64 = base64.b64encode(image_bytes).decode()
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
    generation_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])

        await ensure_bucket()
        object_key = f"uploads/{uid}/{generation_id}.{ext}"
        await minio_client.put_object(
            MINIO_BUCKET,
            object_key,
            io.BytesIO(image_bytes),
            length=len(image_bytes),
            content_type=file.content_type,
        )

        await conn.execute(
            """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
               VALUES ($1, $2, 'New Conversation', '[]'::jsonb, NOW(), NOW())
               ON CONFLICT (id) DO NOTHING""",
            conversation_id, uid,
        )
        await conn.execute(
            """INSERT INTO image_generations
               (id, conversation_id, user_id, mode, source_image_key, status)
               VALUES ($1, $2, $3, 'image_to_text', $4, 'processing')""",
            uuid.UUID(generation_id), conversation_id, uid, object_key,
        )

    # Call vision model via the same OpenAI-compatible API used for chat
    try:
        vision_client = AsyncOpenAI(base_url=VISION_BASE_URL, api_key=VISION_API_KEY)
        response = await vision_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{file.content_type};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = response.choices[0].message.content
        logger.info(f"Vision ({VISION_MODEL}): content={repr(raw[:120] if raw else raw)}")
        result_text = raw or "[Vision model returned no description — check VISION_MODEL and VISION_BASE_URL]"
        img_status = "completed"
        error_message = None
    except Exception as e:
        logger.warning(f"Vision ({VISION_MODEL}) failed: {e}")
        result_text = f"[Image analysis failed: {e}]"
        img_status = "failed"
        error_message = str(e)

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE image_generations
               SET result_text=$1, status=$2, error_message=$3, completed_at=NOW()
               WHERE id=$4""",
            result_text, img_status, error_message, uuid.UUID(generation_id),
        )

    return {
        "generation_id": generation_id,
        "conversation_id": conversation_id,
        "result_text": result_text,
        "status": img_status,
        "image_url": f"{MINIO_PUBLIC_BASE}/{object_key}",
    }


@app.post("/images/generate")
async def image_generate(
    body: ImageGenerateRequest,
    request: Request,
    user: dict = Depends(require_permission("chat")),
):
    pool: asyncpg.Pool = request.app.state.db
    generation_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        await conn.execute(
            """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
               VALUES ($1, $2, 'New Conversation', '[]'::jsonb, NOW(), NOW())
               ON CONFLICT (id) DO NOTHING""",
            body.conversation_id, uid,
        )
        await conn.execute(
            """INSERT INTO image_generations
               (id, conversation_id, user_id, mode, prompt, status)
               VALUES ($1, $2, $3, 'text_to_image', $4, 'processing')""",
            uuid.UUID(generation_id), body.conversation_id, uid, body.prompt,
        )

    effective_prompt = body.prompt
    if body.previous_image_url:
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                img_resp = await http.get(body.previous_image_url)
                img_resp.raise_for_status()
            prev_b64 = base64.b64encode(img_resp.content).decode()
            vision_client = AsyncOpenAI(base_url=VISION_BASE_URL, api_key=VISION_API_KEY)
            vision_resp = await vision_client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{prev_b64}"}},
                        {"type": "text",
                         "text": (
                             f"Describe this image in detail. The user wants to refine it with: "
                             f"'{body.prompt}'. Write a comprehensive text-to-image generation prompt "
                             f"that preserves the key visual elements of the original image and "
                             f"incorporates the requested changes."
                         )},
                    ],
                }],
            )
            effective_prompt = vision_resp.choices[0].message.content
        except Exception:
            effective_prompt = body.prompt

    try:
        async with httpx.AsyncClient(timeout=3600) as http:   # CPU can be slow
            resp = await http.post(
                f"{IMAGE_SERVICE_URL}/generate",
                json={"prompt": effective_prompt, "width": body.width, "height": body.height},
            )
            resp.raise_for_status()
        image_bytes = resp.content

        await ensure_bucket()
        object_key = f"generated/{uid}/{generation_id}.png"
        await minio_client.put_object(
            MINIO_BUCKET,
            object_key,
            io.BytesIO(image_bytes),
            length=len(image_bytes),
            content_type="image/png",
        )
        image_url = f"{MINIO_PUBLIC_BASE}/{object_key}"
        img_status = "completed"
        error_message = None
    except Exception as e:
        image_url = None
        object_key = None
        img_status = "failed"
        error_message = str(e)

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE image_generations
               SET result_image_key=$1, status=$2, error_message=$3, completed_at=NOW()
               WHERE id=$4""",
            object_key, img_status, error_message, uuid.UUID(generation_id),
        )

    if img_status == "failed":
        raise HTTPException(
            status_code=502,
            detail=f"Image generation service unavailable ({IMAGE_SERVICE_URL}): {error_message}",
        )

    return {
        "generation_id": generation_id,
        "conversation_id": body.conversation_id,
        "image_url": image_url,
        "status": img_status,
    }


# --- Documents / RAG routes ---

@app.post("/documents/upload", response_model=DocumentUploadResponse)
async def document_upload(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("chat")),
):
    allowed = {"pdf", "docx", "txt", "md"}
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type: .{ext}")

    pool: asyncpg.Pool = request.app.state.db
    file_bytes = await file.read()
    doc_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        file_key = f"documents/{uid}/{doc_id}.{ext}"
        await ensure_bucket()
        await minio_client.put_object(
            MINIO_BUCKET, file_key,
            io.BytesIO(file_bytes), length=len(file_bytes),
            content_type=file.content_type or "application/octet-stream",
        )
        await conn.execute(
            """INSERT INTO documents (id, user_id, filename, file_type, file_key, status)
               VALUES ($1, $2, $3, $4, $5, 'processing')""",
            uuid.UUID(doc_id), uid, file.filename, ext, file_key,
        )

    try:
        text   = extract_text(file_bytes, ext)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No text extracted from document")

        embed_resp = await embed_client.embeddings.create(
            model=EMBED_MODEL, input=chunks
        )
        vectors = [e.embedding for e in embed_resp.data]

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "document_id": doc_id,
                    "user_id":     uid,
                    "chunk_index": i,
                    "text":        chunk,
                    "filename":    file.filename,
                },
            )
            for i, (vec, chunk) in enumerate(zip(vectors, chunks))
        ]
        await qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET chunk_count=$1, status='ready' WHERE id=$2",
                len(chunks), uuid.UUID(doc_id),
            )
    except Exception as e:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET status='failed', error_message=$1 WHERE id=$2",
                str(e), uuid.UUID(doc_id),
            )
        raise HTTPException(500, f"Processing failed: {e}")

    return DocumentUploadResponse(
        id=doc_id, filename=file.filename, chunk_count=len(chunks), status="ready"
    )


@app.get("/documents")
async def list_documents(
    request: Request,
    user: dict = Depends(require_permission("chat")),
):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid  = await get_user_id(conn, user["username"])
        rows = await conn.fetch(
            """SELECT id, filename, file_type, chunk_count, status, created_at
               FROM documents WHERE user_id=$1 ORDER BY created_at DESC""",
            uid,
        )
    return [dict(r) for r in rows]


@app.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    request: Request,
    user: dict = Depends(require_permission("chat")),
):
    pool: asyncpg.Pool = request.app.state.db
    async with pool.acquire() as conn:
        uid = await get_user_id(conn, user["username"])
        row = await conn.fetchrow(
            "SELECT id, file_key FROM documents WHERE id=$1 AND user_id=$2",
            uuid.UUID(doc_id), uid,
        )
        if not row:
            raise HTTPException(404, "Document not found")
        file_key = row["file_key"]
        await conn.execute("DELETE FROM documents WHERE id=$1", uuid.UUID(doc_id))

    await qdrant.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=FilterSelector(
            filter=Filter(must=[
                FieldCondition(key="document_id", match=MatchValue(value=doc_id))
            ])
        ),
    )

    if file_key:
        try:
            await minio_client.remove_object(MINIO_BUCKET, file_key)
        except Exception as e:
            logger.warning(f"MinIO delete failed for {file_key}: {e}")


# --- Frontend Serving ---

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
async def read_root():
    return FileResponse('static/index.html')
