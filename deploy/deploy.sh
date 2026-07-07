#!/usr/bin/env bash
# Ship a new version: rebuild the image in ACR and roll the Container App to it.
# Run from cable-web/:  bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.env

echo "Rebuilding ${ACR}.azurecr.io/${IMAGE} …"
# --no-logs avoids the Windows CLI log-streaming crash (colorama/cp1252) while
# still waiting for the cloud build and returning its real exit code.
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . --no-logs -o none

ACR_LOGIN=$(az acr show -n "$ACR" --query loginServer -o tsv)
echo "Rolling ${APP} to the new image …"
# The image tag string (:latest) is unchanged, so --image alone is a no-op in Single
# revision mode. --revision-suffix forces a NEW revision, whose replicas re-pull the
# tag and pick up the freshly built digest.
az containerapp update -g "$RG" -n "$APP" \
  --image "${ACR_LOGIN}/${IMAGE}" \
  --revision-suffix "r$(date +%Y%m%d%H%M%S)" -o none

FQDN=$(az containerapp show -g "$RG" -n "$APP" --query 'properties.configuration.ingress.fqdn' -o tsv)
echo "✅ Deployed: https://${FQDN}"
