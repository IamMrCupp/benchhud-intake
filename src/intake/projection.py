"""Projecting an intake ticket into the shape benchhud consumes.

**This module is the privacy boundary, made mechanical.** intake's ``Ticket`` is
a superset carrying every field a repair shop needs — contact details, return
address, serial, condition notes. benchhud gets a deliberately lossy view of it:
the six fields of ``benchhud.tickets.Ticket``, and of the customer, *only the
name*.

Why here, and why so strict: benchhud is the bench workspace, and it can
composite any of its surfaces to a camera. So the rule can't be "hide PII when
streaming" — it has to be "PII never reaches benchhud at all", enforced once, in
code, rather than trusted to a dozen render sites on the other side. Everything
this function omits is a thing that can't leak because benchhud never receives
it.

The ``url`` is the escape hatch: a deep link back into intake, behind intake's
own login, for the on-demand "who is this and how do I reach them" lookup. The
PII lives at the end of that link, not in the payload.
"""

from __future__ import annotations

from intake.store import Ticket

#: The exact keys benchhud.tickets.Ticket expects. Anything not in here does not
#: cross the boundary — full stop.
BENCHHUD_KEYS = ("ref", "device", "symptoms", "customer", "status", "url")


def to_benchhud_ticket(ticket: Ticket, base_url: str) -> dict[str, str]:
    """Reduce an intake ticket to what benchhud may see.

    ``base_url`` is the intake service's own root (e.g. from the request), used
    to build the deep link. Only the customer's *name* represents the person;
    phone, email, return address, serial, accessories, notes and timestamps are
    all dropped on the floor here and never leave this service.
    """
    status = ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status)

    return {
        "ref": ticket.ref,
        "device": ticket.device,
        "symptoms": ticket.symptoms,
        # The name, and nothing else about the customer. This single line is the
        # difference between "benchhud can identify the job" and "customer PII is
        # on a surface that might be streamed".
        "customer": ticket.customer_name,
        "status": status,
        "url": f"{base_url.rstrip('/')}/tickets/{ticket.ref}",
    }
