"""Projecting an intake ticket into the shape benchhud consumes.

**This module is the privacy boundary, made mechanical.** intake's ``Ticket`` is
a superset carrying every field a repair shop needs — contact details, return
address, serial, condition notes. benchhud gets a deliberately lossy view of it:
the six fields of ``benchhud.tickets.Ticket``, and of the customer, *only the
first name*.

Why here, and why so strict: benchhud is the bench workspace, and it can
composite any of its surfaces to a camera. So the rule can't be "hide PII when
streaming" — it has to be "PII never reaches benchhud at all", enforced once, in
code, rather than trusted to a dozen render sites on the other side. Everything
this function omits is a thing that can't leak because benchhud never receives
it.

Why the first name and not the whole one: a bench handle is useful — it tells you
whose board is on the mat, and lets a viewer recognise their own device in chat —
but a *full* name on a streamed surface is an identity leak. The first name is the
narrowest string that still does the job.

The ``url`` is the escape hatch: a deep link back into intake, behind intake's
own login, for the on-demand "who is this and how do I reach them" lookup. The
surname and every contact detail live at the end of that link, not in the payload.
"""

from __future__ import annotations

from intake.store import Ticket

#: The exact keys benchhud.tickets.Ticket expects. Anything not in here does not
#: cross the boundary — full stop.
BENCHHUD_KEYS = ("ref", "device", "symptoms", "customer", "status", "url")


def _first_name(customer_name: str) -> str:
    """The first whitespace-delimited token of a name, or "" if there isn't one.

    A mononym ("Family", "Shop") is its own first token. Hyphenated first names
    ("Jean-Luc") stay whole — only whitespace splits. This is the entire surname
    filter: whatever follows the first space never leaves this service.
    """
    parts = (customer_name or "").split()
    return parts[0] if parts else ""


def to_benchhud_ticket(ticket: Ticket, base_url: str) -> dict[str, str]:
    """Reduce an intake ticket to what benchhud may see.

    ``base_url`` is the intake service's own root (e.g. from the request), used
    to build the deep link. Only the customer's *first name* represents the
    person; the surname, phone, email, return address, serial, accessories, notes
    and timestamps are all dropped on the floor here and never leave this service.
    """
    status = ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status)

    return {
        "ref": ticket.ref,
        "device": ticket.device,
        "symptoms": ticket.symptoms,
        # The first name, and nothing else about the customer. This single line is
        # the difference between "benchhud can identify the job" and "customer PII
        # is on a surface that might be streamed".
        "customer": _first_name(ticket.customer_name),
        "status": status,
        "url": f"{base_url.rstrip('/')}/tickets/{ticket.ref}",
    }
