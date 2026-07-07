#!/usr/bin/env bash
# Azure App Service (Linux) startup command.
# In the portal set Configuration -> General settings -> Startup Command to:
#     bash startup.sh
# Azure sets $PORT; default to 8000 for local parity.
set -e

# Report PDFs are produced by LibreOffice (soffice). Install it once at build
# time (Oryx PRE_BUILD_COMMAND: `apt-get update && apt-get install -y libreoffice-writer`)
# or point SOFFICE_PATH at an existing install. This warns early if it's missing.
command -v soffice >/dev/null 2>&1 || echo "WARN: soffice (LibreOffice) not found — report PDF export will fail until it is installed."

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout 180
