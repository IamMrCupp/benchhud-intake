"""FastAPI application for benchhud-intake."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from intake.db import connect
from intake.schema import initialise
from intake.store import Ticket, TicketStore

app = FastAPI(
    title="benchhud-intake",
    description="Self-hosted repair intake. Holds customer PII — LAN-only, never publicly exposed.",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_db_path() -> Path:
    """Where the database lives.

    On the cluster this points into the NFS PVC; locally it defaults to a file
    beside the repo, which `.gitignore` already covers.
    """
    return Path(os.environ.get("INTAKE_DB_PATH", "intake.db"))


def get_store() -> Iterator[TicketStore]:
    """A store for one request.

    A fresh connection per request rather than a shared one: FastAPI runs sync
    endpoints in a threadpool, and SQLite connections aren't safe to share
    across threads. Cheap for a local file, and the deployment is single-writer
    anyway.

    `initialise()` short-circuits on an up-to-date `user_version`, so calling it
    here costs one read and means the database is always ready regardless of how
    the app was started.
    """
    with connect(get_db_path()) as conn:
        initialise(conn)
        yield TicketStore(conn)


StoreDep = Annotated[TicketStore, Depends(get_store)]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    This route stays **unauthenticated** when the password gate lands in Phase 4
    — it's what Kubernetes hits, and it must answer before a session exists. It
    is the only documented exception to "all routes protected", so it must never
    return anything about a ticket or a customer.
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Placeholder landing page. The intake form replaces this in Phase 2."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/intake", response_class=HTMLResponse)
def intake_form(request: Request) -> HTMLResponse:
    """The intake form — what someone fills in with a customer at the bench."""
    return templates.TemplateResponse(request=request, name="intake.html")


@app.post("/intake")
def create_intake(
    store: StoreDep,
    customer_name: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    return_address: Annotated[str, Form()] = "",
    device: Annotated[str, Form()] = "",
    serial: Annotated[str, Form()] = "",
    symptoms: Annotated[str, Form()] = "",
    accessories: Annotated[str, Form()] = "",
    condition_notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Create a ticket from a submitted form.

    **Redirects rather than rendering.** Showing the confirmation straight from
    the POST means a refresh re-submits — and on a tablet being handed between
    two people, that's a realistic way to end up with two tickets for the same
    board. A duplicate customer record is worse than an error.

    Every value arrives as a string, and untouched optional fields arrive as
    ``""``; the store normalises those to ``None``.
    """
    ticket = store.create_ticket(
        customer_name=customer_name,
        phone=phone,
        email=email,
        return_address=return_address,
        device=device,
        serial=serial,
        symptoms=symptoms,
        accessories=accessories,
        condition_notes=condition_notes,
    )

    return RedirectResponse(url=f"/intake/{ticket.ref}", status_code=303)


@app.get("/intake/{ref}", response_class=HTMLResponse)
def intake_confirmation(request: Request, ref: str, store: StoreDep) -> HTMLResponse:
    """Confirmation after an intake. Its whole job is showing the ref."""
    return templates.TemplateResponse(
        request=request,
        name="confirmation.html",
        context={"ticket": _require_ticket(store, ref)},
    )


def _require_ticket(store: TicketStore, ref: str) -> Ticket:
    """Fetch a ticket for a URL path, or 404.

    A malformed ref raises `ValueError` from the parser — that's a bad URL, so
    it's a 404 rather than a 500.
    """
    try:
        ticket = store.get_by_ref(ref)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such ticket") from None

    if ticket is None:
        raise HTTPException(status_code=404, detail="no such ticket")
    return ticket
