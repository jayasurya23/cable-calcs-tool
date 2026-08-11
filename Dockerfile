# Cable Web — production image for Azure Container Apps.
# Bundles LibreOffice (headless DOCX->PDF) + the Jost fonts + the calc engines,
# so the container is self-contained. Built in the cloud via `az acr build`.
FROM python:3.12-slim

# ── System deps: LibreOffice Writer (provides `soffice`) + fontconfig ────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer \
      libreoffice-core \
      fontconfig \
      fonts-dejavu-core \
      fonts-crosextra-carlito \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Carlito is metric-identical to Calibri: the modern report template's cover
# geometry hangs off Calibri line metrics, and LibreOffice auto-substitutes
# Carlito for Calibri, so Azure renders match Word exactly.

WORKDIR /app

# Python deps first (layer cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the Jost fonts system-wide so LibreOffice renders the report in Jost.
COPY app/static/fonts/*.ttf /usr/share/fonts/truetype/jost/
RUN fc-cache -f

# App code + vendored calc engines.
COPY app ./app
# The in-app /help page renders docs/USER_MANUAL.md at runtime.
COPY docs ./docs
COPY engines ./engines

# Runtime config. DATA_DIR / UPLOAD_DIR default to the Azure Files share mounted
# at /data in Container Apps (the deploy manifest sets these explicitly too).
# HOME=/tmp keeps the LibreOffice profile on fast local disk, not the SMB share.
ENV PYTHONUNBUFFERED=1 \
    ENGINE_DIR=/app/engines \
    DATA_DIR=/data \
    UPLOAD_DIR=/data/uploads \
    COOKIE_SECURE=true \
    HOME=/tmp \
    PORT=8000

EXPOSE 8000

# gunicorn + uvicorn workers. 180s timeout covers the DOCX->PDF conversion.
CMD ["sh", "-c", "gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:${PORT:-8000} --timeout 180"]
