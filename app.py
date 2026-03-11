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
"""


async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(INIT_SQL)

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

@app.post("/chat")
async def chat_endpoint(request: ChatRequest, user: dict = Depends(require_permission("chat"))):
    return StreamingResponse(
        stream_generator(request.messages, request.model),
        media_type="text/plain"
    )


@app.get("/health")
async def health_check():
    return {"status": "ok", "target": API_BASE_URL, "model": MODEL_NAME}


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


# --- Frontend Serving ---

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
async def read_root():
    return FileResponse('static/index.html')
