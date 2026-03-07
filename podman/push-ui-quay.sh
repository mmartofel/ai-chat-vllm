#!/bin/bash

podman tag localhost/ai-chat:latest quay.io/mmartofe/ai-chat:latest

podman push quay.io/mmartofe/ai-chat:latest

echo "Pushed ai-chat to Quay.io"