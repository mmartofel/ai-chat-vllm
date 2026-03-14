#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/user-values.env"

MODEL_PATH="${HF_DIR}/${MODEL_ID}"
export NAMESPACE RHIIS_IMAGE MODEL_ID MODEL_PATH HF_DIR PVC_SIZE

VARS='${NAMESPACE} ${RHIIS_IMAGE} ${MODEL_ID} ${MODEL_PATH} ${HF_DIR} ${PVC_SIZE}'

echo "==> Namespace"
envsubst "$VARS" < "${SCRIPT_DIR}/k8s/namespace.yaml" | oc apply -f -

echo "==> HF token secret"
oc create secret generic hf-token-secret \
  --from-literal=token="${HF_TOKEN}" \
  -n "${NAMESPACE}" --dry-run=client -o yaml | oc apply -f -

echo "==> Remaining manifests"
for f in serviceaccount.yaml pvc-models.yaml deployment.yaml service.yaml route.yaml; do
  envsubst "$VARS" < "${SCRIPT_DIR}/k8s/${f}" | oc apply -f -
done

echo "Done. Watch: oc get pods -n ${NAMESPACE} -w"
