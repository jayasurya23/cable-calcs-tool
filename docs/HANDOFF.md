# Cable Web — Handoff & Operations Runbook

Everything a new maintainer needs to run, deploy, and troubleshoot the SAM
Analysis Report web app. Pair this with:
- [`DEPLOY.md`](../DEPLOY.md) — first-time provisioning + deploy details.
- [`README.md`](../README.md) — architecture.
- [`SAM_REPORT.md`](SAM_REPORT.md) — the SAM module (inputs, calc, assumptions, tests).
- [`USER_MANUAL.md`](USER_MANUAL.md) — the end-user guide.

> **Secrets are never in this repo.** The DB password and Entra client secret live
> only in `deploy/config.env` (gitignored, held by the current owner) and as
> **Container App secrets** in Azure. Do not paste secret values into any doc.

---

## 1. System at a glance

A single **FastAPI** app (server-rendered Jinja + HTMX) that ingests SAM
parametric output and produces a Word/PDF report. Runs on **Azure Container Apps**
(scale-to-zero), builds its image in **Azure Container Registry**, stores generated
documents on **Azure Files**, and keeps relational data in **PostgreSQL**. DOCX→PDF
is done by **LibreOffice** in the image.

- **Live URL:** https://cable-web-castillo.nicesand-bffcb719.eastus2.azurecontainerapps.io
- **Health probe:** `GET /health` → `{"status":"ok", "auth_mode":"entra", …}`
- **Repo:** GitHub `jayasurya23/cable-calcs-tool` (branch `main`)
- **Subscription:** Castillo PMO 360 · **Region:** East US 2

## 2. Access you need (checklist)

- [ ] **Azure** — Contributor on resource group **`cable-web-rg`** (subscription *Castillo PMO 360*). `az login`.
- [ ] **GitHub** — write/admin on `jayasurya23/cable-calcs-tool`.
- [ ] **Entra ID** — ability to manage the app registration **"Cable Calcs Tool"** (for redirect URIs, client-secret rotation, user assignment).
- [ ] **`deploy/config.env`** — the filled config (with secrets) from the current owner. It is **not** in git; without it you can still deploy, but you need it (or the Container App secrets) to re-provision.
- [ ] Local tools: Azure CLI with the `containerapp` extension; Git Bash/WSL. **No Docker needed** (images build in the cloud).

## 3. Azure resource inventory

All in resource group **`cable-web-rg`** (East US 2):

| Resource | Name | Notes |
|---|---|---|
| Container App | `cable-web-castillo` | the app; env `cable-web-env` |
| Container Registry | `cablewebcastilloacr` | image `cableweb:latest` |
| Storage account | `cablewebdocstore` | Azure Files share `cableweb-docs`, mounted at `/data` |
| PostgreSQL Flexible Server | `cable-web-castillo-pg` | DB `cableweb`, admin `cableadmin` (Burstable B1ms) |
| Entra app registration | **Cable Calcs Tool** | tenant `551da9d2-…`, client `758354cf-…`, single-tenant |

**Container App secrets** (names, not values): `database-url`, `acr-password`,
`entra-client-secret`. Env vars of interest: `AUTH_MODE=entra`, `ADMIN_EMAILS`,
`ENTRA_REDIRECT_URI` (set to the prod `/auth/callback`), `COOKIE_SECURE=true`.

## 4. Build & deploy

Images build **in ACR** (`az acr build`) from the working tree — no local Docker.

```bash
cd cable-web
bash deploy/deploy.sh      # rebuild the image + roll the Container App to it
```

- `deploy.sh` only **rebuilds the image and rolls a new revision**. It does **not**
  change env vars or secrets.
- To change **env vars / secrets** (e.g. `ADMIN_EMAILS`, a rotated secret, warm
  schedule), edit `deploy/config.env` and run `bash deploy/provision.sh` (idempotent,
  reconciles all config in §8b), **or** apply a one-off
  `az containerapp update … --set-env-vars …` / `az containerapp secret set …`.
- First-time provisioning of every resource: see [`DEPLOY.md`](../DEPLOY.md).
- Optional CI: `.github/workflows/deploy.yml` (push to `main` → build + roll); needs
  repo secret `AZURE_CREDENTIALS` + vars `AZ_RG/AZ_ACR/AZ_APP/AZ_IMAGE`.

> Windows note: the deploy scripts pass `--no-logs` to `az acr build` because the
> CLI's log streaming crashes on Windows (cp1252). The build still waits and
> returns its real exit code.

## 5. Sign-in & user administration

- **Auth:** Microsoft Entra ID (M365), **single-tenant** → only `@castillope.com`
  accounts can sign in. Users are JIT-provisioned on first sign-in. Graph
  `User.Read` is admin-consented (no per-user consent prompt).
- **Admins** are the emails in `ADMIN_EMAILS` (currently `jbhaskar@` and
  `mpuri@castillope.com`). A user gets the admin role on their **first** sign-in
  after being added; already-provisioned users are promoted from **/admin/users**.
- **Add/replace an admin:** add the email to `ADMIN_EMAILS` in `deploy/config.env`
  and push it to the container:
  ```bash
  az containerapp update -g cable-web-rg -n cable-web-castillo \
    --set-env-vars "ADMIN_EMAILS=jbhaskar@castillope.com,mpuri@castillope.com,new@castillope.com" \
    --revision-suffix "radmin$(date +%Y%m%d%H%M%S)"
  ```
- **Deactivate a user:** admins do it in the app under **Users** (kills their live
  sessions immediately).
- **⚠ Client secret expiry:** the Entra client secret **expires 2028-07-08**.
  Before then, mint a new secret (Entra → App registrations → *Cable Calcs Tool* →
  Certificates & secrets), update `ENTRA_CLIENT_SECRET` in `config.env`, and
  `az containerapp secret set -g cable-web-rg -n cable-web-castillo --secrets entra-client-secret=<new>`
  then roll a revision. If sign-in ever starts failing, check this first.
- **Access scope:** currently *any* org account can sign in. To restrict to named
  people, enable "Assignment required" on the enterprise app and assign users.

## 6. Data, storage & backups

- **Documents** (per revision): Azure Files share `cableweb-docs`, mounted at
  `/data`, under `projects/<project_id>/analyses/<analysis_id>/rev<N>/`
  (PDF, Word, Output workbook, source Excel, pysam, `form.json`). In-progress
  uploads: `/data/uploads/<token>/`. Browse via Azure Portal → Storage account →
  File shares, or Azure Storage Explorer.
- **Relational data** (users, projects, analyses, revisions index, audit log):
  PostgreSQL DB `cableweb`. The files on the share are the documents; the DB is the
  index.
- **Backups:** PostgreSQL Flexible Server has automatic backups (7-day retention by
  default) — restore via *Point-in-time restore* in the Portal. Azure Files is
  locally redundant (Standard_LRS); enable a snapshot/backup policy on the share if
  a stronger RPO is needed.
- **DB migrations** run automatically at boot (`app/core/db.py::_migrate`,
  idempotent, under an advisory lock) — no Alembic. Adding a startup migration must
  never crash boot.

## 7. Monitoring & logs

```bash
# live app logs
az containerapp logs show -g cable-web-rg -n cable-web-castillo --follow
# revision health / which one is serving
az containerapp revision list -g cable-web-rg -n cable-web-castillo \
  --query "[?properties.active].{name:name,running:properties.runningState,health:properties.healthState}" -o table
# quick health
curl -s https://cable-web-castillo.nicesand-bffcb719.eastus2.azurecontainerapps.io/health
```

## 8. Report template maintenance

The report has two styles; **modern** (the AmpCalc cover) is the only one exposed.
It is **built**, not hand-edited:

- Source: `app/modules/sam_report/assets/NewCover.docm` (+ the classic body) →
  `build_modern_template.py` → `report_template_modern.docx`.
- Rebuild: `python app/modules/sam_report/assets/build_modern_template.py`, then
  commit the regenerated `.docx` and deploy.
- **Always verify with a LibreOffice render**, not Word — Azure uses LibreOffice
  and it renders this template differently from Word *and between LO versions*.
  Install LibreOffice locally (`winget install TheDocumentFoundation.LibreOffice`);
  the converter auto-detects it, so local generation then matches Azure.
- History worth knowing: the cover carried a PE-seal image (`media/image2.png`)
  that only older LibreOffice drew — blanked to a transparent pixel in the build.
  The modern TOC is a **baked static result** (LibreOffice won't refresh the index
  on headless convert); it's correct for the fixed 7-section layout.

## 9. Common tasks (playbook)

| Task | How |
|---|---|
| Ship a new version | `bash deploy/deploy.sh` |
| Change env var / secret | edit `deploy/config.env` → `bash deploy/provision.sh` (or a one-off `az containerapp update/secret set`) |
| Add an admin | §5 |
| Rotate Entra secret (before 2028-07-08) | §5 |
| Rotate DB password | reset on the Flexible Server, update `PG_PASSWORD` in `config.env`, `provision.sh` (rebuilds `DATABASE_URL` secret) |
| Adjust warm hours / scaling | edit `WARM_*` / `MIN_REPLICAS` in `config.env` → `provision.sh` |
| Tail logs | §7 |
| Run tests | `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest` |

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Site slow on first hit | Cold start (scaled to zero off-hours). Warm window is Mon–Fri 7am–7pm ET; set `MIN_REPLICAS=1` for always-on. |
| Sign-in fails (AADSTS / redirect) | Check the Entra app's redirect URI matches `ENTRA_REDIRECT_URI` (the prod `/auth/callback`), and that the **client secret hasn't expired** (§5). |
| Report cover/TOC looks wrong | LibreOffice-version rendering. Reproduce with the same LO version family Azure runs; see §8 and `SAM_REPORT.md` §5. |
| Old analysis won't reopen | It self-heals from the latest filed revision; if nothing is filed to rebuild from, a friendly page explains it. |
| Deploy fails | `az login` / correct subscription; ACR build errors surface with `--no-logs` exit code — re-run without `--no-logs` locally to see the stream. |
| DB errors after long idle | `pool_pre_ping`/recycle handle Azure's idle drop; if persistent, check the Flexible Server is running and firewall allows Azure services. |

## 11. Security & cost

- **HTTPS** with a free managed cert; `COOKIE_SECURE=true` (session cookies never
  travel over HTTP). Sessions are DB-backed; only the token **hash** is stored.
- **Single-tenant** Entra restricts sign-in to the org.
- **DB** is `--public-access 0.0.0.0` (reachable from Azure services, not the open
  internet) + enforced TLS + admin password. Tighten with VNet + Private Endpoint
  if required.
- **Secrets** live only in `deploy/config.env` (gitignored) and Container App
  secrets — never in git.
- **Cost:** ~$22–30/month (Container Apps warm-on-schedule + Postgres B1ms + ACR
  Basic + storage). `WARM_REPLICAS=0` drops it a few dollars at the cost of
  off-hours cold starts.

## 12. Known issues / open items

- **LibreOffice-version render fidelity** — see §8 / `SAM_REPORT.md` §5.
- **Modern TOC page numbers are static** (baked), correct for the standard layout.
- **SQLite migration gap** — `_migrate` only swaps the `revisions` unique
  constraint on Postgres; a pre-existing **SQLite** DB keeps the old constraint
  (blocks a 2nd analysis's R0). **Prod is Postgres, so unaffected** — matters only
  for a standalone-SQLite deployment. (Flagged as a follow-up task.)
