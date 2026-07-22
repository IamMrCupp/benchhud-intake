"""FastAPI application for benchhud-intake."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(
    title="benchhud-intake",
    description="Self-hosted repair intake. Holds customer PII — LAN-only, never publicly exposed.",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Placeholder landing page. The intake form replaces this in Phase 2."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    This route stays **unauthenticated** when the password gate lands in Phase 4
    — it's what Kubernetes hits, and it must answer before a session exists. It
    is the only documented exception to "all routes protected", so it must never
    return anything about a ticket or a customer.
    """
    return {"status": "ok"}
