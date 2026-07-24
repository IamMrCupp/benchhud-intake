"""FastAPI application for benchhud-intake."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from intake import auth
from intake.auth import verify_password
from intake.db import connect
from intake.photos import Photo, PhotoError, PhotoStore
from intake.ratelimit import RateLimiter
from intake.schema import initialise
from intake.status import Outcome, Status
from intake.store import Ticket, TicketStore

app = FastAPI(
    title="benchhud-intake",
    description="Self-hosted repair intake. Holds customer PII — LAN-only, never publicly exposed.",
)

#: The only paths reachable without a session.
#:
#: **Deny by default.** Protection is applied to everything and lifted here,
#: rather than added per-route — the inverse arrangement means a route added in
#: a later phase (photo upload, thumbnail download) ships unprotected the moment
#: somebody forgets a decorator, and a silent leak of customer data is a much
#: worse failure than a route that's visibly unreachable.
#:
#: `/healthz` is the Kubernetes probe and must answer before a session can
#: exist; `/login` obviously cannot require being logged in. Nothing else
#: belongs here, and `tests/test_auth_required.py` enumerates the routing table
#: to keep that honest.
EXEMPT_PATHS = frozenset({"/healthz", "/login"})

#: Methods that change state and therefore need a CSRF token.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Failed-login throttle. In-memory and per-process, which is fine while the
#: Deployment stays at `replicas: 1` — see intake/ratelimit.py for what this
#: does and does not protect against.
login_limiter = RateLimiter()


def _client_ip(request: Request) -> str | None:
    """Best-effort client address.

    Deliberately ignores `X-Forwarded-For`: nothing terminates in front of this
    service today, so honouring a client-supplied header would let anyone reset
    their own throttle bucket by changing it. Revisit if a proxy ever lands.
    """
    return request.client.host if request.client else None


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Demand a session for everything outside ``EXEMPT_PATHS``, and a valid
    CSRF token on every state-changing request.

    Both checks are deny-by-default for the same reason: a form added in a later
    phase should be safe without anyone remembering to opt it in.

    ``samesite=lax`` on the session cookie already blocks cross-site POSTs in
    current browsers, and that's most of the protection. This is the
    server-side half — one mechanism enforced by the client isn't where you
    want the only defence for customer records, particularly on whatever
    browser a bench tablet happens to be running.
    """
    # Auth first. A request with no session has nothing worth forging against,
    # and "go and sign in" is a more useful answer than "unverifiable".
    if request.url.path not in EXEMPT_PATHS and not request.session.get("authenticated"):
        target = quote(request.url.path, safe="/")
        return RedirectResponse(url=f"/login?next={target}", status_code=303)

    if request.method in _MUTATING_METHODS:
        # Reading the form here consumes the request stream, so the body has to
        # be replayed or the endpoint downstream sees an empty form and every
        # submission looks like a blank one. Found the hard way: without this,
        # login failed as "wrong password" with a perfectly correct password.
        body = await request.body()

        async def _replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay

        form = await request.form()
        if not auth.csrf_valid(request.session, form.get("csrf_token")):
            return PlainTextResponse(
                "Request could not be verified. Reload the page and try again.",
                status_code=403,
            )

    return await call_next(request)


# Added last, so it wraps the auth middleware — the session has to be decoded
# before anything can check it.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="session",
    https_only=auth.cookie_secure(),
    same_site="lax",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _csrf_context(request: Request) -> dict[str, str]:
    """Make ``csrf_token`` available to every template.

    A context processor rather than a per-route context entry, for the same
    deny-by-default reason as the auth middleware: a template added later gets
    the token without anyone remembering to pass it, and a form that omits the
    hidden field fails visibly at the first POST instead of silently dropping
    protection.
    """
    return {
        "csrf_token": auth.csrf_token(request.session),
        # Distinct from the token: a token exists *before* sign-in, because the
        # login form itself needs one. Keying UI on the token would show a
        # "Sign out" button to someone who has never signed in.
        "authenticated": bool(request.session.get("authenticated")),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_csrf_context])


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


def get_uploads_path() -> Path:
    """Where condition photos live.

    Beside the database on the NFS PVC in production; a local directory
    otherwise, already covered by `.gitignore`.
    """
    return Path(os.environ.get("INTAKE_UPLOADS_PATH", "uploads"))


def get_photo_store() -> PhotoStore:
    return PhotoStore(get_uploads_path())


PhotoStoreDep = Annotated[PhotoStore, Depends(get_photo_store)]


def _attach_photos(photo_store: PhotoStore, ref: str, uploads: list[UploadFile]) -> int:
    """Save each uploaded photo against a ticket, returning how many failed.

    A rejected photo (not an image, too large) is counted and skipped rather
    than raised: the ticket is the record that matters, and a bad photo is
    recoverable by retaking it while a lost intake is not. The store validates
    and cleans up partials, so a failure can't corrupt anything — it just
    doesn't attach.
    """
    failed = 0
    for upload in uploads:
        if not upload.filename:
            continue  # an empty file part — nothing was chosen
        try:
            photo_store.save(ref, upload.filename, upload.file.read())
        except PhotoError:
            failed += 1
    return failed


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


def _safe_next(target: str) -> str:
    """Reduce ``?next=`` to a path on this host, or fall back to the ticket list.

    A login URL is exactly the kind of thing that gets pasted into a message, so
    an unvalidated ``next`` turns a genuine login into a bounce to somebody
    else's page — the classic phishing shape. Only a single-slash absolute path
    is allowed: ``//host`` and ``\\\\host`` are both browser-recognised ways of
    naming another origin, and a scheme (``https:``, ``javascript:``) obviously
    leaves entirely.
    """
    target = (target or "").strip()
    if (
        not target.startswith("/")
        or target.startswith("//")
        or target.startswith("/\\")
        or ":" in target.split("/")[0]
    ):
        return "/tickets"
    return target


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "") -> HTMLResponse:
    """The sign-in page. Reachable without a session, by definition."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": _safe_next(next), "error": None},
    )


@app.post("/login")
def login(
    request: Request,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Check the shared password and start a session.

    A failure says only that the password is wrong — never whether one is
    configured, nor anything about its shape. There's one password here; every
    extra detail narrows a guess.
    """
    client = _client_ip(request)

    if not login_limiter.allowed(client):
        # Checked before the password so a locked-out client can't keep probing,
        # and so lockout costs no scrypt work — otherwise the throttle becomes
        # its own CPU amplifier on a Pi.
        wait = login_limiter.retry_after(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": _safe_next(next),
                # Ceiling division: a 300s wait is "5 minutes", not 6.
                "error": f"Too many attempts. Try again in {-(-wait // 60)} minute(s).",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    if not verify_password(password, auth.configured_hash()):
        login_limiter.record_failure(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": _safe_next(next), "error": "That password is not right."},
            status_code=401,
        )

    login_limiter.record_success(client)
    request.session["authenticated"] = True
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request) -> Response:
    """End the session."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


#: Every field the form posts. Used to echo submitted values back on a
#: rejection, so nothing typed is lost.
_FORM_FIELDS = (
    "customer_name",
    "phone",
    "email",
    "return_address",
    "device",
    "serial",
    "symptoms",
    "accessories",
    "condition_notes",
)


def _render_form(
    request: Request,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the intake form, optionally repopulated with a rejected attempt."""
    return templates.TemplateResponse(
        request=request,
        name="intake.html",
        context={
            "values": {name: (values or {}).get(name, "") for name in _FORM_FIELDS},
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/intake", response_class=HTMLResponse)
def intake_form(request: Request) -> HTMLResponse:
    """The intake form — what someone fills in with a customer at the bench."""
    return _render_form(request)


@app.post("/intake")
def create_intake(
    request: Request,
    store: StoreDep,
    photo_store: PhotoStoreDep,
    photos: Annotated[list[UploadFile], File()] = [],  # noqa: B006 - FastAPI reads this default
    customer_name: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    return_address: Annotated[str, Form()] = "",
    device: Annotated[str, Form()] = "",
    serial: Annotated[str, Form()] = "",
    symptoms: Annotated[str, Form()] = "",
    accessories: Annotated[str, Form()] = "",
    condition_notes: Annotated[str, Form()] = "",
) -> Response:
    """Create a ticket from a submitted form.

    **Redirects rather than rendering.** Showing the confirmation straight from
    the POST means a refresh re-submits — and on a tablet being handed between
    two people, that's a realistic way to end up with two tickets for the same
    board. A duplicate customer record is worse than an error.

    Every value arrives as a string, and untouched optional fields arrive as
    ``""``; the store normalises those to ``None``.
    """
    submitted = {
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "return_address": return_address,
        "device": device,
        "serial": serial,
        "symptoms": symptoms,
        "accessories": accessories,
        "condition_notes": condition_notes,
    }

    try:
        ticket = store.create_ticket(**submitted)
    except ValueError as exc:
        # Hand the form back with everything still in it. The person is at a
        # bench with a customer in front of them; making them re-type a
        # multi-line address because they forgot a phone number is a worse
        # failure than the validation error itself.
        return _render_form(request, values=submitted, error=str(exc), status_code=400)

    failed = _attach_photos(photo_store, ticket.ref, photos)
    suffix = f"?photos_failed={failed}" if failed else ""
    return RedirectResponse(url=f"/intake/{ticket.ref}{suffix}", status_code=303)


@app.get("/tickets", response_class=HTMLResponse)
def ticket_list(request: Request, store: StoreDep, status: str | None = None) -> HTMLResponse:
    """Work that's still on the bench.

    Open tickets by default, built from the derived ``OPEN_STATUSES`` so a newly
    added status can't quietly disappear from this page.

    **No contact details are rendered here.** It's the screen most likely to be
    left open on a bench or read over a shoulder, and the customer name is
    enough to recognise a ticket — phone, email and return address stay on the
    detail page.
    """
    try:
        tickets = (
            store.list_tickets(status=status) if status else store.list_tickets(open_only=True)
        )
    except ValueError as exc:
        # An unknown ?status= is a bad request, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return templates.TemplateResponse(
        request=request,
        name="tickets.html",
        context={
            "tickets": tickets,
            "status": status,
            "all_statuses": [s.value for s in Status],
        },
    )


@app.get("/intake/{ref}", response_class=HTMLResponse)
def intake_confirmation(
    request: Request, ref: str, store: StoreDep, photos_failed: int = 0
) -> HTMLResponse:
    """Confirmation after an intake. Its whole job is showing the ref."""
    return templates.TemplateResponse(
        request=request,
        name="confirmation.html",
        context={"ticket": _require_ticket(store, ref), "photos_failed": photos_failed},
    )


def _render_ticket(
    request: Request,
    ticket: Ticket,
    photos: list[Photo] | None = None,
    error: str | None = None,
    photos_failed: int = 0,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="ticket.html",
        context={
            "ticket": ticket,
            "photos": photos or [],
            "error": error,
            "photos_failed": photos_failed,
            "all_statuses": [s.value for s in Status],
            "all_outcomes": [o.value for o in Outcome],
        },
        status_code=status_code,
    )


@app.get("/tickets/{ref}", response_class=HTMLResponse)
def ticket_detail(
    request: Request, ref: str, store: StoreDep, photo_store: PhotoStoreDep, photos_failed: int = 0
) -> HTMLResponse:
    """Everything about one ticket.

    Unlike the list, this page **does** show contact details — it's the one you
    open deliberately when you need to ring someone about their board.
    """
    ticket = _require_ticket(store, ref)
    return _render_ticket(
        request, ticket, photos=photo_store.list_photos(ref), photos_failed=photos_failed
    )


@app.post("/tickets/{ref}/photos")
def upload_ticket_photos(
    request: Request,
    ref: str,
    store: StoreDep,
    photo_store: PhotoStoreDep,
    photos: Annotated[list[UploadFile], File()] = [],  # noqa: B006 - FastAPI reads this default
) -> Response:
    """Add photos to an existing ticket.

    The realistic flow is "take it in now, photograph it properly once it's on
    the bench", so this exists alongside upload-at-intake. Requiring the ticket
    to exist first means a stray ref can't create an orphan photo directory.
    """
    _require_ticket(store, ref)
    failed = _attach_photos(photo_store, ref, photos)
    suffix = f"?photos_failed={failed}" if failed else ""
    return RedirectResponse(url=f"/tickets/{ref}{suffix}", status_code=303)


@app.post("/tickets/{ref}/status")
def update_ticket_status(
    request: Request,
    ref: str,
    store: StoreDep,
    status: Annotated[str, Form()] = "",
    outcome: Annotated[str, Form()] = "",
) -> Response:
    """Move a ticket through the workflow.

    Redirects on success so a refresh doesn't replay the transition. On a
    rejection — closing with no outcome, an outcome on an open status, an
    unknown status — the page comes back with the error rather than a 500.
    """
    ticket = _require_ticket(store, ref)

    try:
        store.set_status(ref, status, outcome=outcome or None)
    except ValueError as exc:
        return _render_ticket(request, ticket, error=str(exc), status_code=400)

    return RedirectResponse(url=f"/tickets/{ref}", status_code=303)


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
