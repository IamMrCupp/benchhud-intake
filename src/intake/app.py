"""FastAPI application for benchhud-intake."""

from fastapi import FastAPI

app = FastAPI(
    title="benchhud-intake",
    description="Self-hosted repair intake. Holds customer PII — LAN-only, never publicly exposed.",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    This route stays **unauthenticated** when the password gate lands in Phase 4
    — it's what Kubernetes hits, and it must answer before a session exists. It
    is the only documented exception to "all routes protected", so it must never
    return anything about a ticket or a customer.
    """
    return {"status": "ok"}
