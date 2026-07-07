#!/usr/bin/env bash
# Ship a new build: rebuild the image in ACR and roll the Web App. Run this on
# every release after provision.sh has created the infrastructure. From cable-web/:
#     bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.env

echo "Building ${ACR}.azurecr.io/${IMAGE}…"
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . -o none

echo "Restarting ${APP} to pull the new image…"
az webapp restart -g "$RG" -n "$APP" -o none

echo "✅ Deployed → https://${APP}.azurewebsites.net"
