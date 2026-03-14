#!/bin/bash
set -a  # automatically export all variables
source ./user-values.env
# Get the cluster domain name
export CLUSTER_DOMAIN_NAME=`oc get ingresses.config/cluster -o jsonpath='{.spec.domain}'`
set +a

curl -X POST \
   https://vllm-${NAMESPACE}.${CLUSTER_DOMAIN_NAME}/v1/chat/completions \
  -H 'Authorization: Bearer fake-api-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "'"${MODEL_ID}"'",
    "messages": [
      {
        "role": "user",
        "content": "Whats 1 + 1?"
      }
    ]
  }'