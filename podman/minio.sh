# Start MinIO from official image on podman.
#

# podman run -d --name minio \
#  -e MINIO_ROOT_USER=admin \
#  -e MINIO_ROOT_PASSWORD=admin123 \
#  -e MINIO_DEFAULT_BUCKETS=my-bucket \
#  -p 9000:9000 \
#  -p 9001:9001 \
#  -v minio-data:/data \
#  bitnami/minio:latest server /data --console-address ":9001"

podman run -d --name ai-chat-minio \
  -e MINIO_ROOT_USER=admin \
  -e MINIO_ROOT_PASSWORD=admin123 \
  -p 9000:9000 \
  -p 9001:9001 \
  -v minio-data:/data \
  --entrypoint /usr/bin/sh \
  quay.io/minio/minio:latest \
  -c "minio server /data --console-address ':9001' &
      sleep 3 &&
      mc alias set local http://localhost:9000 admin admin123 &&
      mc anonymous set download local/images &&
      mc mb --ignore-existing local/images &&
      wait"
