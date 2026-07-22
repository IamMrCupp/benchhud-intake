"""The app answers a health check.

`/healthz` is the probe Kubernetes will hit for liveness and readiness, and it's
the one route that stays unauthenticated once the password gate lands in Phase 4.
"""

from fastapi.testclient import TestClient

from intake.app import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
