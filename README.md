# AI Chat

A lightweight, browser-based chat interface for local and cloud LLM inference servers. Designed to work with [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), and [Llama Stack](https://github.com/meta-llama/llama-stack) — anything that speaks the OpenAI API.

Scales from a local laptop to production on Red Hat OpenShift without code changes.

## Features

- **Real-time streaming** — responses appear token by token as they arrive
- **Conversation history** — past chats are saved in the browser and listed in a sidebar; switch between them at any time
- **New Chat** — start a fresh conversation without losing previous ones
- **Markdown + syntax highlighting** — full Markdown support with code blocks and a one-click copy button
- **Status bar** — shows the active model name, inference server URL, and last response time
- **Per-message timing** — each assistant reply shows how long it took to generate
- **No build step** — frontend uses CDN-loaded Vue 3, Tailwind CSS, Marked.js, and Highlight.js
- **User authentication** — login form protects the chat; sessions use a signed JWT stored in an httpOnly cookie
- **PostgreSQL-backed users** — user accounts stored in Postgres; default `admin/admin` created on first boot

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| LLM client | `openai` Python library (async streaming) |
| Frontend | Vue.js 3 (Composition API), Tailwind CSS |
| Markdown | Marked.js + Highlight.js |
| Persistence | Browser `localStorage` |
| Auth | `bcrypt` (password hashing), `python-jose` (JWT) |
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

On first boot the app auto-creates the `users` table and seeds an `admin/admin` account (a warning is logged — change this password before exposing the app). There is no CLI tool for managing users.

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
