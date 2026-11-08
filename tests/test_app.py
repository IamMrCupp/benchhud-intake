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
    assert response.json()["status"] == "ok"


def test_healthz_says_which_app_and_version_it_is() -> None:
    """bench-setup tells "something on 8401" from "benchhud-intake on 8401"."""
    from intake import __version__

    response = client.get("/healthz")

    assert response.json() == {
        "status": "ok",
        "app": "benchhud-intake",
        "version": __version__,
    }
