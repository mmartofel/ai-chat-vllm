# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development

Start the dev server (creates venv, installs deps, runs uvicorn on port 8001):
```bash
./dev.sh
```

Or manually:
```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8001
```

Lint Python with ruff:
```bash
ruff check app.py
```

No test runner or frontend build tool — the frontend uses CDN libraries directly.

## Environment

Copy `.env.example` to `.env` and fill in values:
```
# LLM API (any OpenAI-compatible endpoint)
LLM_API_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=mistral

# Database
DB_URL=postgresql://admin:admin@localhost:5432/aichat

# Auth
JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
JWT_EXPIRE_HOURS=8          # optional, default 8
COOKIE_SECURE=false         # set true in production (HTTPS)

# Vision model for image → text (local: moondream, production: llava-v1.6-mistral-7b)
VISION_MODEL=moondream
# Vision API (defaults to LLM_API_URL/LLM_API_KEY if not set)
VISION_BASE_URL=http://localhost:11434/v1   # separate endpoint for vision model
VISION_API_KEY=ollama

# Text → image generation service (local: image_service/, production: GPU deployment)
IMAGE_SERVICE_URL=http://localhost:8100

# MinIO / S3 object storage
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=admin
MINIO_SECRET_KEY=admin123
MINIO_BUCKET=images
MINIO_PUBLIC_BASE_URL=http://localhost:9000/images
```

Supports any OpenAI-compatible API (Ollama, vLLM, Llama Stack).

## Architecture

**Backend** (`app.py`): FastAPI app with the following routes:

### Auth
- `POST /auth/login` — verify credentials; sets HTTP-only, SameSite=strict JWT cookie; returns `{username, role}`
- `POST /auth/logout` — clears the auth cookie
- `GET /auth/me` — returns `{username, role}` for the authenticated user

### Chat
- `POST /chat` — streams LLM responses via the async OpenAI client; accepts `{messages, model (optional)}`; requires `chat` permission

### Server Info
- `GET /health` — returns `{status, target, model}`

### Conversations (require `chat` permission)
- `GET /conversations` — list conversation metadata for the authenticated user (ordered by `updated_at DESC`)
- `GET /conversations/{id}` — fetch a single conversation with full message history
- `PUT /conversations/{id}` — create or update a conversation (upsert); accepts `{title, messages, created_at}`
- `DELETE /conversations/{id}` — delete a conversation; 404 if not found or not owned by caller

### Admin: Users (require `manage_users` permission)
- `GET /admin/users` — list all users with role and active status
- `POST /admin/users` — create a user
- `PUT /admin/users/{id}` — update role/active flag; cannot modify own account
- `DELETE /admin/users/{id}` — delete user; cannot delete own account

### Admin: Roles (require `manage_roles` permission)
- `GET /admin/roles` — list roles with permissions
- `POST /admin/roles` — create a custom role
- `PUT /admin/roles/{id}` — update role name and permissions; built-in names are locked
- `DELETE /admin/roles/{id}` — delete a non-built-in role

### Images (require `chat` permission)
- `POST /images/describe` — accepts `multipart/form-data` (`file`, `conversation_id`, optional `prompt`); calls `VISION_MODEL` via the OpenAI-compatible API; stores source image in MinIO under `uploads/{user_id}/{uuid}.{ext}`; returns `{generation_id, conversation_id, result_text, status, image_url}`
- `POST /images/generate` — accepts JSON (`conversation_id`, `prompt`, `width`, `height`); forwards to `IMAGE_SERVICE_URL/generate`; stores result PNG in MinIO under `generated/{user_id}/{uuid}.png`; returns `{generation_id, conversation_id, image_url, status}`

---

Static files are served from `static/`. PostgreSQL is accessed via an `asyncpg` connection pool created at startup.

### Database Schema

Auto-created on first boot:
- **users**: `id`, `username`, `password_hash`, `is_active`, `created_at`, `role_id`
- **roles**: `id`, `name`, `created_at`
- **permissions**: `id`, `name`
- **role_permissions**: `role_id`, `permission_id`
- **conversations**: `id`, `user_id`, `title`, `messages` (JSONB), `created_at`, `updated_at`; indexed on `(user_id, updated_at DESC)`
- **image_generations**: `id`, `conversation_id`, `user_id`, `mode` (`image_to_text` | `text_to_image`), `prompt`, `source_image_key`, `result_image_key`, `result_text`, `status` (`pending` | `processing` | `completed` | `failed`), `error_message`, `created_at`, `completed_at`

A default `admin/admin` user (and test `moderator_user`/`regular_user`) is inserted if the table is empty — change the admin password immediately.

### Authorization

`require_permission(perm)` FastAPI dependency enforces RBAC. Three built-in roles seeded at startup (idempotent):
- `admin` — all permissions: `chat`, `manage_users`, `manage_roles`, `moderate_content`
- `moderator` — `chat`, `moderate_content`
- `user` — `chat`

Built-in roles cannot be renamed or deleted. Custom roles can be created via the admin panel.

### MinIO Integration

`miniopy-async` client initialized at startup. Automatically creates the bucket with a public read policy on first use. Images are organized by user ID:
- Uploads: `uploads/{user_id}/{uuid}.{ext}`
- Generated: `generated/{user_id}/{uuid}.png`

`MINIO_PUBLIC_BASE_URL` controls the base URL returned to clients (use the public route in production).

---

**Frontend** (`static/`): A single-page app using Vue.js 3 (Composition API), Tailwind CSS v4, Marked.js, and Highlight.js — all loaded via CDN, no build step.

- `app.js` handles auth, message state, streaming via `fetch`, markdown rendering, conversation history, and image upload/generation
- `index.html` is the entry point (two-panel: sidebar + chat)
- Conversations are persisted to PostgreSQL via the REST API; `localStorage` is used as a local cache and cleared on logout
- Sidebar conversations lazy-load their messages on first open; existing localStorage history is offered for migration on first login
- Image upload: file input → POST `/images/describe` → blob URL replaced with permanent MinIO URL
- Image generation: type `/image <prompt>` in chat → POST `/images/generate` → image displayed as assistant message

`admin.html` + `admin.js` provide the admin panel (Vue 3 SPA, Tailwind, no build step) served at `/admin`. Auth-gated: redirects to `/` if role is not `admin`. Two tabs: **Users** (list, create, edit role/active, delete) and **Roles** (list, create, edit name/permissions, delete).

The frontend sends the full conversation history on each request; the backend forwards it directly to the LLM API and streams the response back. Server info (model name, URL) is fetched from `GET /health` on mount.

## Image Service (local text-to-image)

A separate FastAPI process in `image_service/` wraps Hugging Face Diffusers (SDXL-Turbo).
Run it independently from the main app:

```bash
cd image_service
source .venv/bin/activate
uvicorn main:app --port 8100
```

Routes:
- `GET /health` — returns `{status, model}`
- `POST /generate` — accepts `{prompt, width, height, steps}`; returns PNG image

First start downloads ~5 GB of model weights to `~/.cache/huggingface/`. Configure the main app to reach it via `IMAGE_SERVICE_URL` in `.env`. In production on OpenShift this is replaced by a GPU-backed `Deployment`.

Local MinIO for development:
```bash
./podman/minio.sh
```

## Deployment

Container build/push scripts are in `podman/`. Kubernetes/OpenShift manifests are in `deployment/` (uses Kustomize). The container base image is `quay.io/fedora/python-313-minimal`. Target namespace is `vllm-inference`.

```bash
cd podman
./build-ui.sh       # build image
./run-ui-podman.sh  # run locally in container
./push-ui-quay.sh   # push to Quay.io registry
```
