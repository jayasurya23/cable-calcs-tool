#!/usr/bin/env bash
# One-time Azure provisioning for Cable Web (App Service for Containers + ACR +
# PostgreSQL Flexible Server). Idempotent — safe to re-run. Run from cable-web/:
#     bash deploy/provision.sh
# Requires: az CLI logged in (az login), deploy/config.env filled in.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.env

[ -n "${PG_PASSWORD:-}" ] || { echo "ERROR: set PG_PASSWORD in deploy/config.env"; exit 1; }
echo "Subscription: $(az account show --query name -o tsv)"
echo "Provisioning RG=$RG  ACR=$ACR  APP=$APP  PG=$PG_SERVER  in $LOCATION"
echo

exists() { az "$@" >/dev/null 2>&1; }

# Percent-encode a string for safe use inside a URL's userinfo. The DB password
# goes into DATABASE_URL verbatim, so a strong password with @ % / : # ? etc.
# would otherwise corrupt the connection string and the app couldn't reach Postgres.
urlencode() {
  python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1" 2>/dev/null \
    || python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1"
}

# 1) Resource group
az group create -n "$RG" -l "$LOCATION" -o none

# 2) Container registry (admin enabled so App Service can pull with creds)
if ! exists acr show -n "$ACR"; then
  az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true -o none
fi

# 3) Build the image in the cloud (no local Docker needed)
echo "Building image ${ACR}.azurecr.io/${IMAGE} (this takes a few minutes)…"
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . -o none

# 4) PostgreSQL Flexible Server + database.
#    --public-access 0.0.0.0 opens the DB to Azure services (not the public
#    internet). App Service outbound IPs rotate within a shared pool and B1 plans
#    can't use VNet/Private Link, so this is the standard posture here; the admin
#    password + enforced TLS (sslmode=require) are the access controls. To lock it
#    down further, move to a Standard+ plan with VNet integration + a Private
#    Endpoint for the server (see DEPLOY.md → Notes).
if ! exists postgres flexible-server show -g "$RG" -n "$PG_SERVER"; then
  az postgres flexible-server create -g "$RG" -n "$PG_SERVER" -l "$LOCATION" \
    --tier "$PG_TIER" --sku-name "$PG_SKU" --storage-size 32 --version 16 \
    --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
    --public-access 0.0.0.0 --yes -o none
fi
# Create the app database. show-then-create keeps this idempotent while still
# surfacing a genuine create failure (set -e aborts) instead of hiding it.
if ! exists postgres flexible-server db show -g "$RG" -s "$PG_SERVER" -d "$PG_DB"; then
  az postgres flexible-server db create -g "$RG" -s "$PG_SERVER" -d "$PG_DB" -o none
fi

# 5) App Service plan (Linux) + Web App for Containers
if ! exists appservice plan show -g "$RG" -n "$PLAN"; then
  az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku "$PLAN_SKU" -o none
fi
ACR_LOGIN=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)
if ! exists webapp show -g "$RG" -n "$APP"; then
  az webapp create -g "$RG" -p "$PLAN" -n "$APP" \
    --deployment-container-image-name "${ACR_LOGIN}/${IMAGE}" -o none
fi
az webapp config container set -g "$RG" -n "$APP" \
  --docker-custom-image-name "${ACR_LOGIN}/${IMAGE}" \
  --docker-registry-server-url "https://${ACR_LOGIN}" \
  --docker-registry-server-user "$ACR_USER" \
  --docker-registry-server-password "$ACR_PASS" -o none

# 6) App settings (env). /home persists across restarts for docs + uploads.
PG_HOST="${PG_SERVER}.postgres.database.azure.com"
PG_PASSWORD_ENC="$(urlencode "$PG_PASSWORD")"
DATABASE_URL="postgresql+psycopg://${PG_ADMIN}:${PG_PASSWORD_ENC}@${PG_HOST}:5432/${PG_DB}?sslmode=require"
REDIRECT_URI="https://${APP}.azurewebsites.net/auth/callback"
az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
  WEBSITES_PORT=8000 \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=true \
  DATABASE_URL="$DATABASE_URL" \
  DATA_DIR=/home/data \
  UPLOAD_DIR=/home/uploads \
  COOKIE_SECURE=true \
  AUTH_MODE="${AUTH_MODE}" \
  ADMIN_EMAILS="${ADMIN_EMAILS}" \
  ENTRA_TENANT_ID="${ENTRA_TENANT_ID}" \
  ENTRA_CLIENT_ID="${ENTRA_CLIENT_ID}" \
  ENTRA_CLIENT_SECRET="${ENTRA_CLIENT_SECRET}" \
  ENTRA_REDIRECT_URI="$REDIRECT_URI" \
  SESSION_TTL_HOURS="${SESSION_TTL_HOURS}"

az webapp restart -g "$RG" -n "$APP" -o none

echo
echo "✅ Done."
echo "   App:      https://${APP}.azurewebsites.net"
echo "   Health:   https://${APP}.azurewebsites.net/health"
if [ "${AUTH_MODE}" = "local" ]; then
  echo "   Sign in:  open the app and complete /setup to create the admin."
else
  echo "   Entra:    register this Redirect URI on your app registration:"
  echo "             ${REDIRECT_URI}"
fi
