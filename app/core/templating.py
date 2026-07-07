"""Shared Jinja2 templates + static path configuration."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

_APP_DIR = Path(__file__).resolve().parent.parent  # .../cable-web/app
TEMPLATES_DIR = _APP_DIR / "templates"
STATIC_DIR = _APP_DIR / "static"


def _globals(request):
    """Every template sees the signed-in user (set by get_current_user)."""
    return {"user": getattr(request.state, "user", None)}


templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_globals])
