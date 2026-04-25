# Start Qdrant from the official image on podman.
#
# Verify with:
#
#   curl http://localhost:6333/healthz
#
# Web dashboard available at:
#
#   http://localhost:6333/dashboard
#

podman run -d --name ai-chat-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v qdrant-data:/qdrant/storage \
  docker.io/qdrant/qdrant:latest
