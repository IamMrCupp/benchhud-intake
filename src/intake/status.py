"""Where a repair is, and how it ended.

Two fields, deliberately separate:

- ``status`` — *where the thing is*. Eight states, one of them terminal.
- ``outcome`` — *how it ended*. Set only once the ticket closes.

Collapsing them would make ``returned``, ``scrapped`` and ``unrepairable``
sibling statuses, and then "how many boards did I actually fix this year"
becomes archaeology. Two fields now beats a migration later.

The vocabulary is the shop's own — benchhud does no validation on ``status``,
so nothing outside this repo constrains it.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """Where a repair currently sits."""

    #: Agreed to look at it; it's in the post. Boards arrive by mail as often as
    #: by hand, and work you've accepted but not received is the easiest kind to
    #: lose track of.
    AWAITING_ARRIVAL = "awaiting_arrival"

    #: It's here and logged; nobody has opened it yet.
    INTAKE = "intake"

    #: On the bench, finding the fault.
    DIAGNOSING = "diagnosing"

    #: Fault found, price sent, waiting on a yes.
    QUOTED = "quoted"

    #: Approved, blocked on a component.
    AWAITING_PARTS = "awaiting_parts"

    #: Actual work happening.
    REPAIRING = "repairing"

    #: Work finished, waiting to go back to the customer.
    READY = "ready"

    #: Back with the customer, or disposed of. The only terminal state, and the
    #: only one that carries an outcome.
    CLOSED = "closed"


class Outcome(StrEnum):
    """How a repair ended. Only meaningful on a closed ticket."""

    REPAIRED = "repaired"
    UNREPAIRABLE = "unrepairable"
    DECLINED = "declined"
    ABANDONED = "abandoned"


#: Every status that isn't finished.
#:
#: **Derived, never hand-listed.** Phase 5 serves ``GET /api/tickets?status=open``
#: from this set. A hand-maintained list is one forgotten line away from hiding
#: a ticket from the bench queue — which, from the outside, is indistinguishable
#: from losing the ticket.
OPEN_STATUSES: frozenset[Status] = frozenset(Status) - {Status.CLOSED}


def is_open(status: Status) -> bool:
    """True while there's still work to do on this ticket."""
    return status in OPEN_STATUSES


def validate_outcome(status: Status, outcome: Outcome | None) -> None:
    """Enforce: an outcome exists if and only if the ticket is closed.

    Mirrors the ``outcome_iff_closed`` CHECK constraint in the schema. The
    database is the backstop; this is the layer that produces a decent error
    message instead of an ``IntegrityError``.
    """
    if status is Status.CLOSED and outcome is None:
        raise ValueError("closing a ticket requires an outcome")

    if status is not Status.CLOSED and outcome is not None:
        raise ValueError(f"an open ticket cannot carry an outcome (status={status.value!r})")
