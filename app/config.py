from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout (siblings under "Cable optimization/"):
#   cable-web/                <- this web app
#   Cable-Optimisation-app/   <- desktop app holding the shared calc engines
_APP_DIR = Path(__file__).resolve().parent          # .../cable-web/app
_PROJECT_ROOT = _APP_DIR.parent                      # .../cable-web
_REPO_ROOT = _PROJECT_ROOT.parent                    # .../Cable optimization
_DEFAULT_ENGINE_DIR = _REPO_ROOT / "Cable-Optimisation-app"


class Settings(BaseSettings):
    """Loaded from .env (gitignored) or real environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Cable Web"
    app_version: str = "0.1.0"
    debug: bool = False

    # Directory holding the shared desktop calc engines
    # (calculation_engine.py, ac_calculation_engine.py, nec_tables.py).
    # Override on Azure via ENGINE_DIR if the engines ship at a different path.
    engine_dir: str = str(_DEFAULT_ENGINE_DIR)

    # Where uploaded workbooks / pysam JSON are staged for processing.
    upload_dir: str = str(_PROJECT_ROOT / "uploads")

    # Max upload size in bytes (default 50 MB) — SAM run workbooks can be large.
    max_upload_bytes: int = 50 * 1024 * 1024

    # Optional AI reading of module datasheets. Empty key = feature off, and the
    # app behaves exactly as it does without it. Only ever invoked when the CEC
    # database could not identify the module deterministically.
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5"
    ai_datasheet_extraction: bool = True

    # CORS origins (comma-separated in env, "*" for all during early dev).
    cors_origins: str = "*"

    # ── Persistence ──────────────────────────────────────────────────────
    # SQLite by default (standalone). Set DATABASE_URL for Azure SQL/Postgres.
    database_url: str = f"sqlite:///{_PROJECT_ROOT / 'data' / 'app.db'}"
    # Root for durable project/revision document storage.
    data_dir: str = str(_PROJECT_ROOT / "data")

    # ── Authentication ───────────────────────────────────────────────────
    # "entra" = Microsoft Entra ID (M365) sign-in; "local" = built-in accounts.
    auth_mode: str = "local"
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    # Must match the redirect URI on the Entra app registration.
    entra_redirect_uri: str = "http://localhost:8000/auth/callback"
    # Comma-separated emails that become admin on first sign-in (Entra mode).
    admin_emails: str = ""
    session_ttl_hours: int = 72
    # Set true in production (HTTPS) so session cookies never travel over HTTP.
    cookie_secure: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]


settings = Settings()

# Ensure the staging + durable data directories exist.
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
