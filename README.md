# AI Chat

A lightweight, browser-based chat interface for local and cloud LLM inference servers. Designed to work with [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), and [Llama Stack](https://github.com/meta-llama/llama-stack) — anything that speaks the OpenAI API.

Scales from a local laptop to production on Red Hat OpenShift without code changes.

## Features

- **Real-time streaming** — responses appear token by token as they arrive
- **Conversation history** — past chats are persisted in PostgreSQL per user and listed in a sidebar; history survives page refreshes, browser clears, and device switches. Messages lazy-load on first open. Existing localStorage history is offered for migration on first login.
- **New Chat** — start a fresh conversation without losing previous ones
- **Markdown + syntax highlighting** — full Markdown support with code blocks and a one-click copy button
- **Status bar** — shows the active model name, inference server URL, and last response time
- **Per-message timing** — each assistant reply shows how long it took to generate
- **No build step** — frontend uses CDN-loaded Vue 3, Tailwind CSS, Marked.js, and Highlight.js
- **User authentication** — login form protects the chat; sessions use a signed JWT stored in an httpOnly cookie
- **Role-based access control** — three built-in roles (`admin`, `moderator`, `user`) with fine-grained permissions (`chat`, `manage_users`, `manage_roles`, `moderate_content`); custom roles can be added
- **Admin panel** — web UI at `/admin` for managing users and roles; accessible only to admins
- **PostgreSQL-backed users** — user accounts, roles, and permissions stored in Postgres; default `admin/admin` + test users seeded on first boot

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| LLM client | `openai` Python library (async streaming) |
| Frontend | Vue.js 3 (Composition API), Tailwind CSS |
| Markdown | Marked.js + Highlight.js |
| Persistence | PostgreSQL (server) + `localStorage` (client cache) |
| Auth & RBAC | `bcrypt` (passwords), `python-jose` (JWT), role/permission tables in PostgreSQL |
| Database | PostgreSQL via `asyncpg` |
| Deployment | Podman / Docker, Kubernetes / OpenShift |

## Quickstart

**Prerequisites:** Python 3.10+, a running Ollama (or any OpenAI-compatible) inference server, and a PostgreSQL instance (or run `./podman/postgres.sh` for a local one via Podman Desktop).

```bash
git clone <repo-url>
cd ai-chat
./dev.sh
```

Open [http://localhost:8001](http://localhost:8001).

`dev.sh` creates a virtual environment, installs dependencies, and starts the server with auto-reload. By default it connects to a local Ollama instance using the `mistral` model.

### Manual start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8001
```

## Configuration

All configuration is via environment variables or a `.env` file in the project root:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | `ollama` | API key (any string works for local servers) |
| `LLM_MODEL` | `mistral` | Model name to request |
| `DB_URL` | `postgresql://admin:admin@localhost:5432/aichat` | PostgreSQL connection string |
| `JWT_SECRET` | *(required)* | Secret key for signing JWTs — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_EXPIRE_HOURS` | `8` | Session lifetime in hours |
| `COOKIE_SECURE` | `false` | Set `true` in production (HTTPS) to add the `Secure` flag to the auth cookie |

### Common setups

```bash
# Ollama (local)
LLM_API_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=mistral

# vLLM (local or remote)
LLM_API_URL=http://localhost:8000/v1
LLM_API_KEY=token-abc123
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ

# vLLM on OpenShift
LLM_API_URL=http://vllm.vllm-inference.svc.cluster.local:8000/v1
LLM_API_KEY=<service-key>
LLM_MODEL=<deployed-model>
```

## Project Structure

```
app.py               FastAPI backend (streaming, static file serving)
requirements.txt     Python dependencies
dev.sh               Local dev startup script
static/
  index.html         App shell (two-panel layout: sidebar + chat)
  app.js             Vue 3 logic (state, streaming, conversation history)
  style.css          Custom styles (sidebar, status bar, code blocks)
podman/              Container build and push scripts
deployment/          Kubernetes / OpenShift manifests (Kustomize)
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send message history, receive streaming plain-text response (requires auth) |
| `GET` | `/health` | Returns `{ status, target, model }` |
| `GET` | `/` | Serves the frontend |
| `POST` | `/auth/login` | Verify credentials; sets httpOnly JWT cookie |
| `POST` | `/auth/logout` | Clears the auth cookie |
| `GET` | `/auth/me` | Returns `{"username": ...}` for session validation |
| `GET` | `/conversations` | List conversation metadata for the current user (requires auth) |
| `GET` | `/conversations/{id}` | Fetch a single conversation with messages (requires auth) |
| `PUT` | `/conversations/{id}` | Create or update a conversation (requires auth) |
| `DELETE` | `/conversations/{id}` | Delete a conversation (requires auth) |
| `GET` | `/admin/users` | List all users (requires `manage_users`) |
| `POST` | `/admin/users` | Create a user (requires `manage_users`) |
| `PUT` | `/admin/users/{id}` | Update user role / active flag (requires `manage_users`) |
| `DELETE` | `/admin/users/{id}` | Delete a user (requires `manage_users`) |
| `GET` | `/admin/roles` | List roles with permissions (requires `manage_roles`) |
| `POST` | `/admin/roles` | Create a custom role (requires `manage_roles`) |
| `PUT` | `/admin/roles/{id}` | Update role name and permissions (requires `manage_roles`) |
| `DELETE` | `/admin/roles/{id}` | Delete a non-built-in role (requires `manage_roles`) |

`POST /chat` requires a valid session cookie and returns `401` if no valid cookie is present.

`POST /chat` body:
```json
{
  "messages": [
    { "role": "user", "content": "Hello!" }
  ]
}
```

## User Management

On first boot the app auto-creates the `users`, `roles`, `permissions`, `role_permissions`, and `conversations` tables. Three built-in roles are seeded at every boot (idempotent): `admin` (all permissions: `chat`, `manage_users`, `manage_roles`, `moderate_content`), `moderator` (`chat`, `moderate_content`), and `user` (`chat`). Built-in roles cannot be renamed or deleted (enforced server-side).

If the `users` table is empty, three accounts are seeded: `admin/admin`, `moderator_user/moderator`, and `regular_user/user`. A warning is logged — change the `admin` password before exposing the app.

For ongoing user and role management, use the admin panel at `/admin` (requires an account with the `admin` role). The panel provides a **Users** tab (create, edit role/active, delete) and a **Roles** tab (create custom roles, assign permissions, delete).

## Linting

```bash
ruff check app.py
```

## Container & Deployment

```bash
cd podman
./build-ui.sh       # build image
./run-ui-podman.sh  # run locally in container
./push-ui-quay.sh   # push to Quay.io registry
```

OpenShift deployment uses Kustomize (`deployment/kustomization.yaml`). The container image is based on `quay.io/fedora/python-313-minimal` and published to `quay.io/mmartofe/ai-chat`.
# ai-chat-vllm
