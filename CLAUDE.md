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
```

Supports any OpenAI-compatible API (Ollama, vLLM, Llama Stack).

## Architecture

**Backend** (`app.py`): FastAPI app with two routes — `POST /chat` streams LLM responses using the async OpenAI client, and `GET /health`. Static files are served from `static/`.

**Frontend** (`static/`): A single-page app using Vue.js 3 (Composition API), Tailwind CSS, Marked.js, and Highlight.js — all loaded via CDN, no build step. `app.js` handles message state, streaming via `fetch`, markdown rendering, and conversation history persisted to `localStorage`. `index.html` is the entry point.

The frontend sends the full conversation history on each request; the backend forwards it directly to the LLM API and streams the response back. Conversation history is stored as `ai-chat-conversations` in `localStorage`. Server info (model name, URL) is fetched from `GET /health` on mount.

## Deployment

Container build/push scripts are in `podman/`. Kubernetes/OpenShift manifests are in `deployment/` (uses Kustomize). The container base image is `quay.io/fedora/python-313-minimal`.
