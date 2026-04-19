#!/bin/bash
# Deploy the application to OpenShift using the kustomize configuration in the deployment directory

oc new-project vllm-inference

cd deployment/chat-vllm
./deploy.sh

cd ../..
oc apply -k ./deployment   
