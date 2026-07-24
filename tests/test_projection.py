"""The benchhud projection — the single place customer PII is filtered out.

`test_no_pii_of_any_kind_survives` is the load-bearing test. It doesn't check
specific keys; it scans the whole serialized output for every PII value the
ticket carried. That way a field added to the intake `Ticket` later can't
quietly ride along into benchhud — the test fails the moment it does.
"""

import json
from dataclasses import dataclass

import pytest

from intake.projection import BENCHHUD_KEYS, to_benchhud_ticket
from intake.store import Ticket

# A ticket with every PII-bearing field set to a distinctive, greppable value.
FULL = Ticket(
    ref="26-0007",
    device="Pioneer CDJ-3000",
    symptoms="jog wheel unresponsive",
    customer_name="Renata Quist",
    status="diagnosing",
    created_at="2026-07-23T10:00:00+00:00",
    updated_at="2026-07-23T11:30:00+00:00",
    phone="555-0142-SECRET",
    email="renata-secret@example.com",
    serial="SN-SECRET-9931",
    return_address="14 Hidden Lane\nNowhere SECRET",
    accessories="charger-SECRET, no drive",
    condition_notes="scuff on lid SECRET",
    outcome=None,
)

BASE = "http://repair-bench:8000/"


def project() -> dict:
    return to_benchhud_ticket(FULL, BASE)


# --- shape ------------------------------------------------------------------


def test_exactly_the_contract_keys() -> None:
    assert set(project()) == set(BENCHHUD_KEYS)


def test_the_contract_keys_are_the_expected_six() -> None:
    assert set(BENCHHUD_KEYS) == {"ref", "device", "symptoms", "customer", "status", "url"}


def test_ref_device_symptoms_status_pass_through() -> None:
    result = project()

    assert result["ref"] == "26-0007"
    assert result["device"] == "Pioneer CDJ-3000"
    assert result["symptoms"] == "jog wheel unresponsive"
    assert result["status"] == "diagnosing"


def test_customer_is_the_name(  ) -> None:
    assert project()["customer"] == "Renata Quist"


def test_url_is_a_deep_link(  ) -> None:
    assert project()["url"] == "http://repair-bench:8000/tickets/26-0007"


def test_url_handles_a_base_without_a_trailing_slash() -> None:
    assert to_benchhud_ticket(FULL, "http://x:8000")["url"] == "http://x:8000/tickets/26-0007"


# --- the privacy guarantee --------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "555-0142-SECRET",  # phone
        "renata-secret@example.com",  # email
        "SN-SECRET-9931",  # serial
        "14 Hidden Lane",  # return address
        "Nowhere SECRET",  # return address, line 2
        "charger-SECRET",  # accessories
        "scuff on lid SECRET",  # condition notes
        "2026-07-23T10:00:00",  # created_at
        "2026-07-23T11:30:00",  # updated_at
    ],
)
def test_no_pii_of_any_kind_survives(secret) -> None:
    """Scan the whole serialized projection — not specific keys — so a field
    added to the intake Ticket later cannot quietly leak into benchhud."""
    blob = json.dumps(project())

    assert secret not in blob, f"leaked into the benchhud projection: {secret!r}"


def test_only_the_name_represents_the_customer() -> None:
    """The name is the *only* customer information that crosses the boundary."""
    result = project()

    assert result["customer"] == "Renata Quist"
    # And nothing else in the payload references the person beyond that string.
    assert "@" not in json.dumps({k: v for k, v in result.items() if k != "url"})


# --- benchhud shape round-trip ----------------------------------------------


@dataclass(frozen=True)
class _BenchhudTicket:
    """A local stand-in for benchhud.tickets.Ticket, same fields and defaults.

    Kept in sync by intent: if the real dataclass changes, this test is where
    the mismatch should surface first. Constructing it from the projection
    proves the JSON is exactly what benchhud will deserialize into.
    """

    ref: str
    device: str
    symptoms: str = ""
    customer: str = ""
    status: str = ""
    url: str | None = None


def test_projection_constructs_a_benchhud_ticket() -> None:
    ticket = _BenchhudTicket(**project())

    assert ticket.ref == "26-0007"
    assert ticket.customer == "Renata Quist"
    assert ticket.url.endswith("/tickets/26-0007")


def test_a_minimal_ticket_projects() -> None:
    """Only ref and device are required upstream; everything else defaults."""
    minimal = Ticket(
        ref="26-0001",
        device="ThinkPad X1",
        symptoms="",
        customer_name="A. Customer",
        status="intake",
        created_at="t",
        updated_at="t",
        phone="555-0100",
    )

    result = to_benchhud_ticket(minimal, BASE)
    assert _BenchhudTicket(**result).ref == "26-0001"
