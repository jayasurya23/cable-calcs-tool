# Deploying Cable Web to Azure

Target: **Azure App Service for Containers** (Linux, B1) + **Azure Container
Registry** + **PostgreSQL Flexible Server**. The image is built *in the cloud*
(`az acr build`) — **no local Docker required**.

```
 Dockerfile ──az acr build──▶ ACR ──pull──▶ App Service (Linux container)
 (LibreOffice + Jost + app)                        │
                                                   ├── /home  (persistent: docs + uploads)
                                                   └── DATABASE_URL ──▶ PostgreSQL Flexible Server
```

## Prerequisites
- Azure CLI logged in: `az login` (you're on subscription **Castillo PMO 360**).
- Run commands from the `cable-web/` directory (Git Bash / WSL / Azure Cloud Shell).

## 1. Configure
```bash
cp deploy/config.env.example deploy/config.env
# edit deploy/config.env:
#   - ACR, APP, PG_SERVER must be GLOBALLY UNIQUE
#   - set a strong PG_PASSWORD
#   - AUTH_MODE=local for the first boot (switch to entra later)
```

## 2. Provision + first deploy (one command, idempotent)
```bash
bash deploy/provision.sh
```
Creates: resource group, ACR (Basic), builds the image, PostgreSQL Flexible
Server (Burstable B1ms) + database, App Service plan (B1 Linux), and the Web App
— then sets all env vars and starts it. Prints the URL.

**Approx. monthly cost:** App Service B1 ≈ $13 · PostgreSQL B1ms ≈ $12–15 ·
ACR Basic ≈ $5 → **~$30–35/mo**.

## 3. First sign-in
- `AUTH_MODE=local`: open `https://<APP>.azurewebsites.net` → `/setup` creates
  the admin account. Add engineers under **Users**.

## 4. Switch to Microsoft (M365) sign-in — when ready
1. **Entra ID → App registrations → New registration**
   - Redirect URI (Web): `https://<APP>.azurewebsites.net/auth/callback`
   - **Certificates & secrets → New client secret** (copy the *value*)
   - **API permissions → Microsoft Graph → Delegated → User.Read**
2. Put the values in `deploy/config.env` (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`,
   `ENTRA_CLIENT_SECRET`, `AUTH_MODE=entra`, `ADMIN_EMAILS=you@castillo…`) and
   re-run `bash deploy/provision.sh` (it only updates settings).

## 5. Ship new versions
```bash
bash deploy/deploy.sh          # rebuild image in ACR + roll the app
```
Or wire the included **GitHub Actions** pipeline (`.github/workflows/deploy.yml`):
push to `main` → build + deploy. Add repo secret `AZURE_CREDENTIALS`
(`az ad sp create-for-rbac --sdk-auth --role Contributor --scopes
/subscriptions/<sub>/resourceGroups/cable-web-rg`) and repo variables
`AZ_RG`, `AZ_ACR`, `AZ_APP`, `AZ_IMAGE`.

## Notes
- **Persistence**: the SQLite path is *not* used in prod — data lives in
  PostgreSQL; generated documents live under `/home/data` (persistent because
  `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`).
- **HTTPS**: App Service gives `*.azurewebsites.net` a free cert; `COOKIE_SECURE=true`
  is set so session cookies never travel over plain HTTP. Add a custom domain +
  managed cert in the portal if desired.
- **DB network access**: the server is provisioned with `--public-access 0.0.0.0`
  (reachable from Azure services, *not* the open internet), guarded by the admin
  password + enforced TLS. App Service outbound IPs rotate in a shared pool and
  the B1 plan can't use VNet, so this is the standard posture at this tier. To
  tighten it, move to a **Standard+** App Service plan with **VNet integration**
  and a **Private Endpoint** on the Postgres server, then remove the allow-Azure
  firewall rule.
- **DB password**: any strong password works — it's URL-encoded before being put
  into `DATABASE_URL`, so special characters (`@ % / : # ?`) are safe.
- **Calc engines**: bundled into the image at `/app/engines`
  (`ENGINE_DIR`); re-sync from the desktop app with `engines/README.md`.
- **Logs**: `az webapp log tail -g cable-web-rg -n <APP>`.
