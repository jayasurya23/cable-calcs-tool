#!/usr/bin/env bash
# One-time Azure provisioning for Cable Web on **Azure Container Apps** (Consumption)
# + Azure Container Registry + PostgreSQL Flexible Server + Azure Files (docs).
# Same hosting model as the QAQC automation site: no fixed App Service plan, and
# the app scales to zero when idle. Idempotent — safe to re-run. Run from cable-web/:
#     bash deploy/provision.sh
# Requires: az CLI logged in (az login) with the containerapp extension, and
# deploy/config.env filled in.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.env

# Force UTF-8 for the Azure CLI's stdout. On Windows (esp. when output is piped)
# Python defaults to the cp1252 codec, which crashes `az acr build` with a
# UnicodeEncodeError while streaming non-ASCII build-log output. Harmless on
# Linux / Azure Cloud Shell.
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

[ -n "${PG_PASSWORD:-}" ] || { echo "ERROR: set PG_PASSWORD in deploy/config.env"; exit 1; }
echo "Subscription: $(az account show --query name -o tsv)"
echo "Provisioning RG=$RG  ENV=$ENV  APP=$APP  ACR=$ACR  PG=$PG_SERVER  in $LOCATION"
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

# 2) Container registry (admin enabled so Container Apps can pull with creds)
if ! exists acr show -n "$ACR"; then
  az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true -o none
fi

# 3) Build the image in the cloud (no local Docker needed).
#    --no-logs: skip streaming the build logs. On Windows the streamed non-ASCII log
#    output crashes the CLI (colorama/cp1252 UnicodeEncodeError). --no-logs still
#    WAITS for the cloud build and returns its real exit code, so a genuine build
#    failure still aborts here.
echo "Building image ${ACR}.azurecr.io/${IMAGE} (this takes a few minutes)…"
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . --no-logs -o none

# 4) PostgreSQL Flexible Server + database.
#    --public-access 0.0.0.0 opens the DB to Azure services (Container Apps included),
#    not the public internet. Container egress IPs rotate in a shared pool and this
#    tier can't use VNet, so this is the standard posture; admin password + enforced
#    TLS (sslmode=require) are the controls. See DEPLOY.md → Notes to lock down further.
if ! exists postgres flexible-server show -g "$RG" -n "$PG_SERVER"; then
  az postgres flexible-server create -g "$RG" -n "$PG_SERVER" -l "$LOCATION" \
    --tier "$PG_TIER" --sku-name "$PG_SKU" --storage-size 32 --version 16 \
    --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
    --public-access 0.0.0.0 --yes -o none
fi
# show-then-create keeps this idempotent while still surfacing a genuine create
# failure (set -e aborts) instead of hiding it.
if ! exists postgres flexible-server db show -g "$RG" -s "$PG_SERVER" -d "$PG_DB"; then
  az postgres flexible-server db create -g "$RG" -s "$PG_SERVER" -d "$PG_DB" -o none
fi

# 5) Storage account + file share for generated documents (persist across replicas
#    and scale-to-zero cycles; Postgres holds the relational data).
if ! exists storage account show -g "$RG" -n "$STORAGE_ACCT"; then
  az storage account create -g "$RG" -n "$STORAGE_ACCT" -l "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 -o none
fi
SA_KEY=$(az storage account keys list -g "$RG" -n "$STORAGE_ACCT" --query '[0].value' -o tsv)
if ! exists storage share-rm show -g "$RG" --storage-account "$STORAGE_ACCT" -n "$SHARE"; then
  az storage share-rm create -g "$RG" --storage-account "$STORAGE_ACCT" -n "$SHARE" \
    --quota 16 -o none
fi

# 6) Container Apps environment (auto-provisions a Log Analytics workspace)
if ! exists containerapp env show -g "$RG" -n "$ENV"; then
  az containerapp env create -g "$RG" -n "$ENV" -l "$LOCATION" -o none
fi
# Wait until the environment is ready — bounded (~10 min) and abort on a terminal
# failure state or persistent CLI error instead of looping forever.
echo "Waiting for environment to be ready…"
STATE=""; tries=0
while [ "$tries" -lt 60 ]; do
  STATE=$(az containerapp env show -g "$RG" -n "$ENV" --query 'properties.provisioningState' -o tsv 2>/dev/null || echo "")
  case "$STATE" in
    Succeeded) break ;;
    Failed|Canceled) echo "ERROR: environment provisioning ended in state '$STATE'"; exit 1 ;;
  esac
  tries=$((tries + 1)); sleep 10
done
[ "$STATE" = "Succeeded" ] || { echo "ERROR: environment not ready after ~10 min (last state='${STATE:-unknown}')"; exit 1; }

# 7) Attach the file share to the environment under the logical name 'cablewebdocs'
#    (referenced by the app's volume.storageName below).
az containerapp env storage set -g "$RG" -n "$ENV" --storage-name cablewebdocs \
  --azure-file-account-name "$STORAGE_ACCT" --azure-file-account-key "$SA_KEY" \
  --azure-file-share-name "$SHARE" --access-mode ReadWrite -o none

# 8) Resolve identifiers + build the connection string.
ENV_ID=$(az containerapp env show -g "$RG" -n "$ENV" --query id -o tsv)
ACR_LOGIN=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)
PG_HOST="${PG_SERVER}.postgres.database.azure.com"
PG_PASSWORD_ENC="$(urlencode "$PG_PASSWORD")"
DATABASE_URL="postgresql+psycopg://${PG_ADMIN}:${PG_PASSWORD_ENC}@${PG_HOST}:5432/${PG_DB}?sslmode=require"

# 8a) First run only: create the app + volume mount from a minimal manifest. We do
#     NOT put the (possibly empty) Entra secret or the FQDN-dependent redirect URI
#     here — those, and all subsequent config changes, are applied imperatively in
#     8b so re-runs actually take effect (`az containerapp update --yaml` only
#     reconciles template:, not configuration.secrets/ingress/registries).
if ! exists containerapp show -g "$RG" -n "$APP"; then
  MANIFEST="$(mktemp)"
  trap 'rm -f "$MANIFEST"' EXIT
  cat > "$MANIFEST" <<YAML
location: ${LOCATION}
name: ${APP}
type: Microsoft.App/containerApps
properties:
  managedEnvironmentId: ${ENV_ID}
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: 8000
      transport: auto
      allowInsecure: false
    secrets:
      - name: database-url
        value: "${DATABASE_URL}"
      - name: acr-password
        value: "${ACR_PASS}"
    registries:
      - server: ${ACR_LOGIN}
        username: ${ACR_USER}
        passwordSecretRef: acr-password
  template:
    containers:
      - name: cableweb
        image: ${ACR_LOGIN}/${IMAGE}
        resources:
          cpu: ${CPU}
          memory: ${MEMORY}
        env:
          - name: DATABASE_URL
            secretRef: database-url
          - name: DATA_DIR
            value: /data
          - name: UPLOAD_DIR
            value: /data/uploads
        volumeMounts:
          - volumeName: docs
            mountPath: /data
    scale:
      minReplicas: ${MIN_REPLICAS}
      maxReplicas: ${MAX_REPLICAS}
      rules:
        - name: http-scale
          http:
            metadata:
              concurrentRequests: "50"
        - name: business-hours
          custom:
            type: cron
            metadata:
              timezone: "${WARM_TZ}"
              start: "${WARM_START}"
              end: "${WARM_END}"
              desiredReplicas: "${WARM_REPLICAS}"
    volumes:
      - name: docs
        storageType: AzureFile
        storageName: cablewebdocs
YAML
  az containerapp create -g "$RG" -n "$APP" --yaml "$MANIFEST" -o none
  rm -f "$MANIFEST"; trap - EXIT
fi

# 8b) Reconcile ALL config imperatively — runs on both first-create and re-runs, so
#     a later `AUTH_MODE=entra` re-run actually updates secrets + env.
# Secrets: DB URL + ACR password always; Entra secret only when provided (Azure
# rejects empty-valued secrets).
SECRETS=( "database-url=${DATABASE_URL}" "acr-password=${ACR_PASS}" )
if [ -n "${ENTRA_CLIENT_SECRET:-}" ]; then
  SECRETS+=( "entra-client-secret=${ENTRA_CLIENT_SECRET}" )
fi
# The AI datasheet-reader key is a secret too — never a plain env var.
if [ -n "${OPENAI_API_KEY:-}" ]; then
  SECRETS+=( "openai-api-key=${OPENAI_API_KEY}" )
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  SECRETS+=( "anthropic-api-key=${ANTHROPIC_API_KEY}" )
fi
az containerapp secret set -g "$RG" -n "$APP" --secrets "${SECRETS[@]}" -o none
# The registry itself is configured once in the create manifest (registries[] ->
# passwordSecretRef: acr-password). It references the secret by name, so refreshing
# the 'acr-password' secret value above is all that's needed if the ACR creds rotate
# — no separate 'registry set' call (and its flags) required.

# FQDN exists once ingress is created; use it for the Entra redirect URI so M365
# sign-in doesn't fall back to the localhost default.
FQDN=$(az containerapp show -g "$RG" -n "$APP" --query 'properties.configuration.ingress.fqdn' -o tsv)
# Once a custom domain is bound, sign-in must return the user THERE — otherwise
# Entra bounces them back to the azurecontainerapps.io URL after authenticating
# and they end up on the old address. Falls back to the default FQDN when unset.
SITE_HOST="${CUSTOM_DOMAIN:-$FQDN}"
REDIRECT_URI="https://${SITE_HOST}/auth/callback"

# Always-meaningful vars. Possibly-empty ones (admin/entra) are added only when set,
# so we never pass a bare "KEY=" to --set-env-vars.
ENVVARS=(
  "DATA_DIR=/data"
  "UPLOAD_DIR=/data/uploads"
  "COOKIE_SECURE=true"
  "PORT=8000"
  "AUTH_MODE=${AUTH_MODE}"
  "ENTRA_REDIRECT_URI=${REDIRECT_URI}"
  "SESSION_TTL_HOURS=${SESSION_TTL_HOURS}"
  "DATABASE_URL=secretref:database-url"
)
if [ -n "${ADMIN_EMAILS:-}" ];       then ENVVARS+=( "ADMIN_EMAILS=${ADMIN_EMAILS}" ); fi
if [ -n "${ENTRA_TENANT_ID:-}" ];    then ENVVARS+=( "ENTRA_TENANT_ID=${ENTRA_TENANT_ID}" ); fi
if [ -n "${ENTRA_CLIENT_ID:-}" ];    then ENVVARS+=( "ENTRA_CLIENT_ID=${ENTRA_CLIENT_ID}" ); fi
if [ -n "${ENTRA_CLIENT_SECRET:-}" ]; then ENVVARS+=( "ENTRA_CLIENT_SECRET=secretref:entra-client-secret" ); fi
# Optional AI datasheet reader. Unset = the feature stays off in the container.
if [ -n "${OPENAI_API_KEY:-}" ];    then ENVVARS+=( "OPENAI_API_KEY=secretref:openai-api-key" ); fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then ENVVARS+=( "ANTHROPIC_API_KEY=secretref:anthropic-api-key" ); fi
if [ -n "${AI_PROVIDER:-}" ];       then ENVVARS+=( "AI_PROVIDER=${AI_PROVIDER}" ); fi
if [ -n "${AI_MODEL:-}" ];          then ENVVARS+=( "AI_MODEL=${AI_MODEL}" ); fi
if [ -n "${AI_DATASHEET_EXTRACTION:-}" ]; then ENVVARS+=( "AI_DATASHEET_EXTRACTION=${AI_DATASHEET_EXTRACTION}" ); fi
# --revision-suffix forces a NEW revision every run, so the freshly built image tag
# is actually re-pulled (an unchanged :latest string alone would be a no-op).
#
# MSYS_NO_PATHCONV=1 is CRITICAL on Git Bash / MSYS (Windows): without it, Git Bash
# rewrites the POSIX value "/data" in "DATA_DIR=/data" into a Windows path
# ("C:/Program Files/Git/data"), so the app writes documents to ephemeral container
# disk instead of the mounted Azure Files share — and every revision roll / scale-to-
# zero then WIPES all filed reports. Harmless on Linux / Cloud Shell. The other args
# here (image ref, URLs, secretrefs) aren't affected by path conversion either way.
MSYS_NO_PATHCONV=1 az containerapp update -g "$RG" -n "$APP" \
  --image "${ACR_LOGIN}/${IMAGE}" \
  --cpu "$CPU" --memory "$MEMORY" \
  --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
  --set-env-vars "${ENVVARS[@]}" \
  --revision-suffix "r$(date +%Y%m%d%H%M%S)" -o none

echo
echo "✅ Done."
echo "   App:      https://${FQDN}"
echo "   Health:   https://${FQDN}/health"
if [ "${AUTH_MODE}" = "local" ]; then
  echo "   Sign in:  open the app and complete /setup to create the admin."
else
  echo "   Entra:    register this Redirect URI on your app registration:"
  echo "             ${REDIRECT_URI}"
fi
