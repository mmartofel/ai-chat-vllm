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
- **No build step** — frontend uses CDN-loaded Vue 3, Tailwind CSS v4, Marked.js, and Highlight.js
- **User authentication** — login form protects the chat; sessions use a signed JWT stored in an httpOnly cookie
- **Role-based access control** — three built-in roles (`admin`, `moderator`, `user`) with fine-grained permissions (`chat`, `manage_users`, `manage_roles`, `moderate_content`); custom roles can be added
- **Admin panel** — web UI at `/admin` for managing users and roles; accessible only to admins
- **PostgreSQL-backed users** — user accounts, roles, and permissions stored in Postgres; default `admin/admin` seeded on first boot
- **Image upload & analysis** — attach any image in chat; a vision model (moondream / llava) describes it and responds in context; image stored persistently in MinIO
- **Text-to-image generation** — type `/image <prompt>` to generate images via a local SDXL-Turbo service or a GPU-backed OpenShift deployment; result displayed inline in chat
- **Image audit trail** — all image operations tracked in a dedicated `image_generations` table with status, prompts, and storage keys

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| LLM client | `openai` Python library (async streaming) |
| Vision model | Any OpenAI-compatible vision endpoint (moondream, llava, etc.) |
| Image generation | SDXL-Turbo via `image_service/` (Hugging Face Diffusers) |
| Object storage | MinIO / S3-compatible via `miniopy-async` |
| Frontend | Vue.js 3 (Composition API), Tailwind CSS v4 |
| Markdown | Marked.js + Highlight.js |
| Persistence | PostgreSQL (server) + `localStorage` (client cache) |
| Auth & RBAC | `bcrypt` (passwords), `python-jose` (JWT), role/permission tables in PostgreSQL |
| Database | PostgreSQL via `asyncpg` |
| Deployment | Podman / Docker, Kubernetes / OpenShift |

## Quickstart

**Prerequisites:** Python 3.10+, a running Ollama (or any OpenAI-compatible) inference server, a PostgreSQL instance (or run `./podman/postgres.sh`), and a MinIO instance (or run `./podman/minio.sh`).

```bash
git clone <repo-url>
cd ai-chat-vllm
cp .env.example .env   # edit as needed
./dev.sh
```

Open [http://localhost:8001](http://localhost:8001).

`dev.sh` creates a virtual environment, installs dependencies, and starts the server with auto-reload.

### Manual start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8001
```

### Local infrastructure (Podman)

```bash
./podman/postgres.sh   # start PostgreSQL
./podman/minio.sh      # start MinIO (ports 9000 / 9001)
```

### Image generation service (optional, CPU)

```bash
cd image_service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8100
```

First start downloads ~5 GB of SDXL-Turbo weights. Required only if you use the `/image` command.

## Configuration

All configuration via environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | `ollama` | API key (any string works for local servers) |
| `LLM_MODEL` | `mistral` | Chat model name |
| `DB_URL` | `postgresql://admin:admin@localhost:5432/aichat` | PostgreSQL connection string |
| `JWT_SECRET` | *(required)* | Secret key for signing JWTs |
| `JWT_EXPIRE_HOURS` | `8` | Session lifetime in hours |
| `COOKIE_SECURE` | `false` | Set `true` in production (HTTPS) |
| `VISION_MODEL` | `moondream` | Vision model for image → text (must be served on `LLM_API_URL`) |
| `IMAGE_SERVICE_URL` | `http://localhost:8100` | Text-to-image service base URL |
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO / S3 endpoint |
| `MINIO_ACCESS_KEY` | `admin` | MinIO access key |
| `MINIO_SECRET_KEY` | `admin123` | MinIO secret key |
| `MINIO_BUCKET` | `images` | Bucket name for all image storage |
| `MINIO_PUBLIC_BASE_URL` | `http://localhost:9000/images` | Public base URL returned to clients for viewing images |

Generate `JWT_SECRET` with:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Common setups

```bash
# Ollama (local)
LLM_API_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=mistral
VISION_MODEL=moondream

# vLLM (local or remote)
LLM_API_URL=http://localhost:8000/v1
LLM_API_KEY=token-abc123
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ
VISION_MODEL=llava-v1.6-mistral-7b

# vLLM on OpenShift
LLM_API_URL=http://vllm.vllm-inference.svc.cluster.local:8000/v1
LLM_API_KEY=<service-key>
LLM_MODEL=<deployed-model>
IMAGE_SERVICE_URL=http://image-service.vllm-inference.svc.cluster.local:8100
MINIO_ENDPOINT=https://<odf-route>
MINIO_PUBLIC_BASE_URL=https://<odf-route>/images
COOKIE_SECURE=true
```

## Project Structure

```
app.py               FastAPI backend (auth, chat, conversations, images)
requirements.txt     Python dependencies
dev.sh               Local dev startup script
.env.example         Environment variable template
static/
  index.html         App shell (sidebar + chat)
  app.js             Vue 3 logic (streaming, history, image upload/generation)
  admin.html         Admin panel shell
  admin.js           Admin panel logic (users, roles)
  style.css          Custom styles
image_service/       Standalone SDXL-Turbo text-to-image FastAPI service
podman/              Container build/push scripts + local infra scripts
deployment/          Kubernetes / OpenShift manifests (Kustomize)
```

## API

### Auth
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/login` | Verify credentials; sets httpOnly JWT cookie; returns `{username, role}` |
| `POST` | `/auth/logout` | Clears the auth cookie |
| `GET` | `/auth/me` | Returns `{username, role}` for the current session |

### Chat & Health
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send message history; receive streaming plain-text response (requires `chat`) |
| `GET` | `/health` | Returns `{status, target, model}` |
| `GET` | `/` | Serves the frontend |

### Conversations (require `chat`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/conversations` | List conversation metadata for the current user |
| `GET` | `/conversations/{id}` | Fetch a single conversation with messages |
| `PUT` | `/conversations/{id}` | Create or update a conversation (upsert) |
| `DELETE` | `/conversations/{id}` | Delete a conversation |

### Images (require `chat`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/images/describe` | Upload image (`multipart/form-data`); analyze with vision model; store in MinIO; returns `{generation_id, result_text, status, image_url}` |
| `POST` | `/images/generate` | Generate image from text (`{prompt, width, height}`); store in MinIO; returns `{generation_id, image_url, status}` |

### Admin: Users (require `manage_users`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/users` | List all users |
| `POST` | `/admin/users` | Create a user |
| `PUT` | `/admin/users/{id}` | Update user role / active flag |
| `DELETE` | `/admin/users/{id}` | Delete a user |

### Admin: Roles (require `manage_roles`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/roles` | List roles with permissions |
| `POST` | `/admin/roles` | Create a custom role |
| `PUT` | `/admin/roles/{id}` | Update role name and permissions |
| `DELETE` | `/admin/roles/{id}` | Delete a non-built-in role |

`POST /chat` body:
```json
{
  "messages": [
    { "role": "user", "content": "Hello!" }
  ]
}
```

`POST /images/describe` body: `multipart/form-data` with fields `file` (image), `conversation_id`, and optional `prompt`.

`POST /images/generate` body:
```json
{
  "conversation_id": "uuid",
  "prompt": "a red fox in a snowy forest",
  "width": 512,
  "height": 512
}
```

## Image Features

### Upload & Analyze

Click the paperclip icon (or similar upload button) in the chat input to attach an image. The app:
1. Displays the image immediately using a local blob URL
2. Uploads it to `/images/describe` (multipart form)
3. The backend sends the image to the configured `VISION_MODEL` for analysis
4. Stores the image permanently in MinIO at `uploads/{user_id}/{uuid}.{ext}`
5. Replaces the temporary blob URL with the permanent MinIO URL in the conversation
6. Returns the vision model's description as an assistant message

### Text-to-Image

Type `/image <your prompt>` in the chat input to generate an image. The app:
1. Sends the prompt to `/images/generate`
2. The backend forwards it to `IMAGE_SERVICE_URL/generate` (SDXL-Turbo)
3. Stores the result PNG in MinIO at `generated/{user_id}/{uuid}.png`
4. Displays the generated image inline as an assistant message

Default dimensions: 512×512 px.

### Image Audit Trail

All image operations are tracked in the `image_generations` table:
- `mode`: `image_to_text` (describe) or `text_to_image` (generate)
- `status`: `pending` → `processing` → `completed` / `failed`
- Source/result MinIO object keys, prompt, error messages, and timestamps

## User Management

On first boot the app auto-creates all tables. Three built-in roles are seeded at every boot (idempotent): `admin` (all permissions), `moderator` (`chat`, `moderate_content`), and `user` (`chat`). Built-in roles cannot be renamed or deleted.

If the `users` table is empty, three accounts are seeded: `admin/admin`, `moderator_user/moderator`, and `regular_user/user`. **Change the `admin` password before exposing the app.**

For ongoing management, use the admin panel at `/admin` (requires `admin` role).

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

OpenShift deployment uses Kustomize (`deployment/kustomization.yaml`). Target namespace: `vllm-inference`. The container image is based on `quay.io/fedora/python-313-minimal` and published to `quay.io/mmartofe/ai-chat`.

Deployed components:
- **chat-ui** — main FastAPI app (`quay.io/mmartofe/ai-chat:latest`, port 8001)
- **image-service** — CPU SDXL-Turbo image generation (`quay.io/mmartofe/ai-chat-image-service:latest`, port 8100); PVC `image-service-model-cache` (10 Gi) persists model weights across pod restarts
- **minio** — object storage for uploaded and generated images
- **postgres** — conversation and user persistence
