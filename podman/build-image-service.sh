
cd ../image_service

podman build -t ai-chat-image-service:latest -f ../podman/Containerfile-image-service .
