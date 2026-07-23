"""Shared test configuration.

The app resolves its session secret at **import** time, which means importing
`intake.app` without a configured password raises. That's deliberate — a
misconfigured deployment should crashloop visibly rather than start up serving
customer records to anyone who asks — but it does mean the test suite has to
configure itself the same way a deployment does.

Set here rather than per-module so no future test file can accidentally import
the app unconfigured and get a confusing error a long way from the cause.
"""

import os

from intake.auth import COOKIE_SECURE_ENV_VAR, ENV_VAR, SESSION_ENV_VAR, hash_password

TEST_PASSWORD = "bench password"

# Hashed once at collection: scrypt is deliberately not cheap, and this runs
# before every test module that touches the app.
os.environ.setdefault(ENV_VAR, hash_password(TEST_PASSWORD))
os.environ.setdefault(SESSION_ENV_VAR, "test-session-secret-not-a-real-one")
os.environ.setdefault(COOKIE_SECURE_ENV_VAR, "0")


def sign_in(client):
    """Give a test client a session.

    Every route except `/healthz` and `/login` needs one, so any test about page
    content has to authenticate first. Tests *about* the gate deliberately don't
    call this — see `tests/test_auth_required.py`.
    """
    response = client.post("/login", data={"password": TEST_PASSWORD})
    assert response.status_code in (200, 303), "test sign-in failed"
    return client
