# Cable Web — production image for Azure App Service for Containers.
# Bundles LibreOffice (headless DOCX->PDF) + the Jost fonts + the calc engines,
# so the container is self-contained. Built in the cloud via `az acr build`.
FROM python:3.12-slim

# ── System deps: LibreOffice Writer (provides `soffice`) + fontconfig ────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer \
      libreoffice-core \
      fontconfig \
      fonts-dejavu-core \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (layer cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the Jost fonts system-wide so LibreOffice renders the report in Jost.
COPY app/static/fonts/*.ttf /usr/share/fonts/truetype/jost/
RUN fc-cache -f

# App code + vendored calc engines.
COPY app ./app
COPY engines ./engines

# Runtime config. DATA_DIR / UPLOAD_DIR point at the App Service persistent
# /home mount (enabled via WEBSITES_ENABLE_APP_SERVICE_STORAGE=true).
ENV PYTHONUNBUFFERED=1 \
    ENGINE_DIR=/app/engines \
    DATA_DIR=/home/data \
    UPLOAD_DIR=/home/uploads \
    COOKIE_SECURE=true \
    HOME=/tmp \
    PORT=8000

EXPOSE 8000

# gunicorn + uvicorn workers. 180s timeout covers the DOCX->PDF conversion.
CMD ["sh", "-c", "gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:${PORT:-8000} --timeout 180"]
