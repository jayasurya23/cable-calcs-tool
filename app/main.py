from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.db import init_db
from app.core.deps import Forbidden, NeedsLogin, require_user
from app.core.models import User
from app.core.templating import STATIC_DIR, templates
from app.modules.auth.router import admin_router, router as auth_router
from app.modules.projects.router import router as projects_router
from app.modules.sam_report.router import router as sam_router
from app.shared.engine import ENGINES_AVAILABLE, ENGINE_IMPORT_ERROR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Keep the CEC module database current: on startup, if the cached copy is
    # missing or older than a week, pull the latest from NREL's SAM library in the
    # background (also happens on report generation). No manual CSV upload needed.
    try:
        from app.modules.sam_report import cec_db
        cec_db.maybe_refresh_async()
    except Exception:  # noqa: BLE001 - never block startup on the refresh
        pass
    yield


# API docs stay available in debug/dev; hidden in production (they'd expose the
# full API surface unauthenticated).
_docs = {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"} \
    if settings.debug else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title=settings.app_name, version=settings.app_version,
              lifespan=lifespan, **_docs)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Open routes: sign-in/setup + health + static. Everything else requires a user.
app.include_router(auth_router)
app.include_router(admin_router)          # admin gate enforced per-endpoint
app.include_router(projects_router)       # require_user enforced per-endpoint
app.include_router(sam_router, dependencies=[Depends(require_user)])


@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, exc: NeedsLogin):
    """Browsers get a redirect to /login; HTMX requests get HX-Redirect."""
    login_url = f"/login?next={exc.next_url}"
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": login_url})
    return RedirectResponse(login_url, status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    return HTMLResponse("<h1>403 — admin access required</h1>", status_code=403)


DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request, user: User = Depends(require_user)):
    """The user manual, served from docs/USER_MANUAL.md so there is ONE source of
    truth — editing the markdown updates the in-app help."""
    src = DOCS_DIR / "USER_MANUAL.md"
    try:
        import markdown
        body = markdown.markdown(
            src.read_text(encoding="utf-8"),
            extensions=["tables", "fenced_code", "toc", "sane_lists"])
        return templates.TemplateResponse(request, "help.html", {"body": body})
    except FileNotFoundError:
        return templates.TemplateResponse(request, "help.html", {
            "error": "The user manual isn't bundled with this build."}, status_code=404)
    except Exception as exc:  # noqa: BLE001 - help must never 500 the app
        return templates.TemplateResponse(request, "help.html", {
            "error": f"The user manual could not be rendered: {exc}"}, status_code=500)


@app.get("/health")
def health():
    """Liveness probe (used by Azure App Service health checks)."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "engines_available": ENGINES_AVAILABLE,
        "engine_error": (str(ENGINE_IMPORT_ERROR) if ENGINE_IMPORT_ERROR else None),
        "auth_mode": settings.auth_mode,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: User = Depends(require_user)):
    """Landing page / module launcher."""
    modules = [
        {
            "name": "Projects",
            "href": "/projects",
            "desc": "Project control: cover-sheet details, SAM analyses, and the "
                    "revision history of every generated document.",
            "ready": True,
        },
        {
            "name": "Cable Calculations",
            "href": "#",
            "desc": "DC / AC / MV cable sizing & voltage drop. Coming to the web.",
            "ready": False,
        },
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "modules": modules,
            "engines_available": ENGINES_AVAILABLE,
        },
    )
