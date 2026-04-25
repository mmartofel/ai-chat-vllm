#!/bin/bash
# Deploy the application to OpenShift using the kustomize configuration in the deployment directory

oc new-project vllm-inference

# Grant anyuid SCC to the qdrant service account so the pod can run as UID 1000
# (Qdrant's official image requires a fixed UID for storage ownership)
oc adm policy add-scc-to-user anyuid \
  -z qdrant \
  -n vllm-inference

cd deployment/chat-vllm
./deploy.sh

cd ../image-vllm
./deploy.sh

cd ../embed-vllm
./deploy.sh

cd ../..
oc apply -k ./deployment

# MinIO route has no hardcoded hostname — OpenShift assigns one based on the cluster domain.
# Discover it and patch MINIO_PUBLIC_BASE_URL in the chat-ui ConfigMap so image URLs resolve.
echo "Waiting for MinIO route hostname..."
MINIO_HOST=""
for i in $(seq 1 20); do
  MINIO_HOST=$(oc get route chat-minio-api -n vllm-inference -o jsonpath='{.spec.host}' 2>/dev/null)
  [ -n "$MINIO_HOST" ] && break
  sleep 3
done

if [ -z "$MINIO_HOST" ]; then
  echo "ERROR: Could not get MinIO route hostname. Set MINIO_PUBLIC_BASE_URL in the chat-ui ConfigMap manually."
  exit 1
fi

echo "MinIO hostname: $MINIO_HOST"
oc patch configmap chat-ui -n vllm-inference --type merge \
  -p "{\"data\":{\"MINIO_PUBLIC_BASE_URL\":\"https://${MINIO_HOST}/images\"}}"
oc rollout restart deployment/chat-ui -n vllm-inference
echo "Done — MINIO_PUBLIC_BASE_URL patched and chat-ui restarted."
