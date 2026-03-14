#!/bin/bash

podman tag localhost/ai-chat-image-service:latest quay.io/mmartofe/ai-chat-image-service:latest

podman push quay.io/mmartofe/ai-chat-image-service:latest

echo "Pushed ai-chat-image-service to Quay.io"