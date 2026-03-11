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

Copy `.env` (not committed) with:
```
LLM_API_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=mistral

DB_URL=postgresql://admin:admin@localhost:5432/aichat
JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
JWT_EXPIRE_HOURS=8          # optional, default 8
COOKIE_SECURE=false         # set true in production (HTTPS)
```

Supports any OpenAI-compatible API (Ollama, vLLM, Llama Stack).

## Architecture

**Backend** (`app.py`): FastAPI app with the following routes:
- `POST /auth/login` / `POST /auth/logout` — session management; JWT stored in an HTTP-only, SameSite=strict cookie
- `GET /auth/me` — returns the currently authenticated user
- `POST /chat` — streams LLM responses via the async OpenAI client; requires a valid JWT cookie
- `GET /health` — server info (model name, URL)
- `GET /conversations` — list conversation metadata for the authenticated user (ordered by `updated_at DESC`)
- `GET /conversations/{id}` — fetch a single conversation with full message history
- `PUT /conversations/{id}` — create or update a conversation (upsert)
- `DELETE /conversations/{id}` — delete a conversation; 404 if not found or not owned by caller
- `GET /admin/users` — list all users with role (requires `manage_users`)
- `POST /admin/users` — create a user (requires `manage_users`)
- `PUT /admin/users/{id}` — update role/active flag; cannot modify own account (requires `manage_users`)
- `DELETE /admin/users/{id}` — delete user; cannot delete own account (requires `manage_users`)
- `GET /admin/roles` — list roles with permissions (requires `manage_roles`)
- `POST /admin/roles` — create a custom role (requires `manage_roles`)
- `PUT /admin/roles/{id}` — update role name and permissions; built-in names are locked (requires `manage_roles`)
- `DELETE /admin/roles/{id}` — delete a non-built-in role (requires `manage_roles`)

Static files are served from `static/`. PostgreSQL is accessed via an `asyncpg` connection pool created at startup. The schema (`users`, `roles`, `permissions`, `role_permissions`, and `conversations` tables) is auto-created on first boot, and a default `admin/admin` user is inserted if the table is empty (a warning is logged — change the password immediately). `json` is used from the stdlib for conversation serialization (no new pip deps).

Authorization uses `require_permission(perm)` as a FastAPI dependency. Three built-in roles are seeded at startup: `admin` (all permissions), `moderator` (`chat`, `moderate_content`), `user` (`chat`). Built-in roles cannot be renamed or deleted. Custom roles can be created via the admin panel.

**Frontend** (`static/`): A single-page app using Vue.js 3 (Composition API), Tailwind CSS, Marked.js, and Highlight.js — all loaded via CDN, no build step. `app.js` handles message state, streaming via `fetch`, markdown rendering, and conversation history. Conversations are persisted to PostgreSQL via the REST API; `localStorage` is used as a local cache and cleared on logout. Sidebar conversations lazy-load their messages on first open. `index.html` is the entry point.

`admin.html` + `admin.js` provide the admin panel (Vue 3 SPA, Tailwind, no build step) served at `/admin`. It is auth-gated — on mount it calls `GET /auth/me` and redirects to `/` if the user's role is not `admin`. The panel has two tabs: **Users** (list, create, edit role/active, delete) and **Roles** (list, create, edit name/permissions, delete).

The frontend sends the full conversation history on each request; the backend forwards it directly to the LLM API and streams the response back. Server info (model name, URL) is fetched from `GET /health` on mount.

## Deployment

Container build/push scripts are in `podman/`. Kubernetes/OpenShift manifests are in `deployment/` (uses Kustomize). The container base image is `quay.io/fedora/python-313-minimal`.
