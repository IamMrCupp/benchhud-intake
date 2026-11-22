"""A ping when a repair request comes in. Optional, and it says almost nothing.

A request made at 2am is no use if nobody sees it until they next happen to
open this app. So when ``INTAKE_NOTIFY_URL`` is set, a new request POSTs one
line to it: enough to make a phone buzz, through whatever you already run
(ntfy, a Discord or Slack webhook, Home Assistant).

**This is the only outbound call this service makes, so it carries no customer
data at all.** Not a name, not a device, not the request's id: just that
something is waiting, and how many. The details stay behind the login, where
they were. A notification service is somebody else's computer, and usually
somebody else's phone lock screen.

**It can't hurt the request.** The ping is sent after the relay has its
``202``, with a short timeout, and every failure is swallowed. A dead webhook
means a missed buzz and a count in the nav, never a lost request.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import urllib.request

URL_ENV_VAR = "INTAKE_NOTIFY_URL"
FORMAT_ENV_VAR = "INTAKE_NOTIFY_FORMAT"

#: ``json`` suits Slack, Discord and Home Assistant; ``text`` is ntfy's.
FORMATS = ("json", "text")

TIMEOUT_SECONDS = 5

_log = logging.getLogger("intake.notify")


def notify_url() -> str | None:
    """Where to send the ping, or ``None`` when notifications are off.

    http(s) only. ``urllib`` will happily open ``file:`` and friends, and a
    typo in a setting shouldn't turn this into something that reads the disk.
    """
    value = os.environ.get(URL_ENV_VAR, "").strip()
    return value if value.startswith(("https://", "http://")) else None


def notify_format() -> str:
    value = os.environ.get(FORMAT_ENV_VAR, "").strip().lower()
    return value if value in FORMATS else "json"


def message(pending: int) -> str:
    """The whole notification. A fixed sentence and a count."""
    waiting = "1 waiting" if pending == 1 else f"{pending} waiting"
    return f"benchhud-intake: new repair request ({waiting})"


def build(pending: int, fmt: str) -> tuple[bytes, str]:
    """The request body and its content type."""
    text = message(pending)
    if fmt == "text":
        return text.encode(), "text/plain; charset=utf-8"
    # One body for the common receivers: Slack-style hooks read `text`,
    # Discord-style ones read `content`.
    body = {"text": text, "content": text, "pending": pending}
    return json.dumps(body).encode(), "application/json"


def request_received(pending: int) -> bool:
    """Send the ping, if one is configured. Returns whether it was delivered.

    Never raises. The log line on failure names the kind of error and not the
    URL, which for most of these services is itself the secret.
    """
    url = notify_url()
    if url is None:
        return False

    body, content_type = build(pending, notify_format())
    # The scheme was checked in notify_url: http(s) and nothing else.
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # OSError covers urllib's own errors, refused connections and timeouts.
        # HTTPException is the layer below: a URL with a space in it, or an
        # answer that isn't HTTP. Left uncaught, its message (which quotes the
        # URL) would land in the server log from the background task.
        _log.warning("request notification failed: %s", type(exc).__name__)
        return False
