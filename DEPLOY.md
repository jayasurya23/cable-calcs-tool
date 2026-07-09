# Deploying Cable Web to Azure

Target: **Azure Container Apps** (Consumption) + **Azure Container Registry** +
**PostgreSQL Flexible Server** + **Azure Files** (for generated documents). Same
hosting model as the QAQC automation site. The image is built *in the cloud*
(`az acr build`) — **no local Docker required** — and the app **scales to zero**
when idle, so you only pay for compute while it's actually in use.

```
 Dockerfile ──az acr build──▶ ACR ──pull──▶ Container App (scales 0→N)
 (LibreOffice + Jost + app)                        │
                                                   ├── /data   (Azure Files: docs + uploads)
                                                   └── DATABASE_URL ──▶ PostgreSQL Flexible Server
```

## Prerequisites
- Azure CLI logged in: `az login` (you're on subscription **Castillo PMO 360**).
- The `containerapp` CLI extension (installed automatically on first use).
- Run commands from the `cable-web/` directory (Git Bash / WSL / Azure Cloud Shell).

## 1. Configure
```bash
cp deploy/config.env.example deploy/config.env
# edit deploy/config.env:
#   - ACR, STORAGE_ACCT, PG_SERVER must be GLOBALLY UNIQUE
#   - set a strong PG_PASSWORD (any characters — it's URL-encoded automatically)
#   - AUTH_MODE=local for the first boot (switch to entra later)
```

## 2. Provision + first deploy (one command, idempotent)
```bash
bash deploy/provision.sh
```
Creates: resource group, ACR (Basic), builds the image, PostgreSQL Flexible
Server (Burstable B1ms) + database, a Storage account + file share, a Container
Apps environment, attaches the share, and creates the Container App — then prints
the live URL (`https://<app>.<hash>.<region>.azurecontainerapps.io`).

**Approx. monthly cost:** Container Apps (1 replica warm on the work-hours cron
schedule, scale-to-zero otherwise) ≈ $5–10 · PostgreSQL B1ms ≈ $12–15 · ACR Basic
≈ $5 · Storage/Log Analytics ≈ $1–2 → **~$22–30/mo** (vs ~$30–35 on a fixed App
Service plan). Set `WARM_REPLICAS=0` for pure scale-to-zero (~$17–22/mo, but a slow
first load after idle).

## 3. First sign-in
- `AUTH_MODE=local`: open the printed URL → `/setup` creates the admin account.
  Add engineers under **Users**.
- First hit after the app has been idle takes a few seconds to cold-start
  (scale-from-zero). To avoid that entirely, set `MIN_REPLICAS=1` in
  `config.env` and re-run provision (adds a small always-on compute cost).

## 4. Switch to Microsoft (M365) sign-in — when ready
1. **Entra ID → App registrations → New registration**
   - Redirect URI (Web): `https://<app-fqdn>/auth/callback` (the printed URL + `/auth/callback`)
   - **Certificates & secrets → New client secret** (copy the *value*)
   - **API permissions → Microsoft Graph → Delegated → User.Read**
2. Put the values in `deploy/config.env` (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`,
   `ENTRA_CLIENT_SECRET`, `AUTH_MODE=entra`, `ADMIN_EMAILS=you@castillo…`) and
   re-run `bash deploy/provision.sh` (it updates the app in place).

## 5. Ship new versions
```bash
bash deploy/deploy.sh          # rebuild image in ACR + roll the Container App
```
Or wire the included **GitHub Actions** pipeline (`.github/workflows/deploy.yml`):
push to `main` → build + roll. Add repo secret `AZURE_CREDENTIALS`
(`az ad sp create-for-rbac --sdk-auth --role Contributor --scopes
/subscriptions/<sub>/resourceGroups/cable-web-rg`) and repo variables
`AZ_RG`, `AZ_ACR`, `AZ_APP`, `AZ_IMAGE`.

## Notes
- **Persistence**: relational data lives in PostgreSQL; generated documents live
  on the mounted Azure Files share at `/data` (survives restarts, new revisions,
  and scale-to-zero). SQLite is only used for local dev.
- **Warm-up / cold starts**: a cron scale rule keeps `WARM_REPLICAS` warm
  `WARM_START`–`WARM_END` (default 7am–7pm Mon–Fri `WARM_TZ`), so there are no
  cold starts during work hours. Outside the window it scales to `MIN_REPLICAS`
  (0); an off-hours visit still works but cold-starts (~10–40s for the LibreOffice
  image). An `http-scale` rule is kept alongside so requests always activate the
  app. Adjust the schedule in `config.env` and re-run `provision.sh`.
- **HTTPS**: Container Apps gives the app FQDN a free managed cert;
  `COOKIE_SECURE=true` is set so session cookies never travel over plain HTTP.
  Add a custom domain + managed cert with `az containerapp hostname` if desired.
- **DB network access**: the server is provisioned with `--public-access 0.0.0.0`
  (reachable from Azure services, *not* the open internet), guarded by the admin
  password + enforced TLS. Container Apps egress IPs rotate in a shared pool and
  this tier can't use VNet, so this is the standard posture. To tighten it, move
  to a VNet-integrated Container Apps environment + a Private Endpoint on Postgres.
- **DB password**: any strong password works — it's URL-encoded before being put
  into `DATABASE_URL`, so special characters (`@ % / : # ?`) are safe.
- **Calc engines**: bundled into the image at `/app/engines` (`ENGINE_DIR`);
  re-sync from the desktop app with `engines/README.md`.
- **Logs**: `az containerapp logs show -g cable-web-rg -n <APP> --follow`.
