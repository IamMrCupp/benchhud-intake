"""board_id on a ticket, and its doc links."""

import pytest

from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore


@pytest.fixture
def store(tmp_path):
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)
        yield TicketStore(conn, year=26)


@pytest.fixture
def ref(store):
    return store.create_ticket(
        device="Pioneer CDJ-3000", customer_name="DJ in LA", phone="555-0100"
    ).ref


# --- board_id ---------------------------------------------------------------


def test_board_id_defaults_to_none(store, ref) -> None:
    assert store.get_by_ref(ref).board_id is None


def test_board_id_can_be_set_at_intake(store) -> None:
    ticket = store.create_ticket(
        device="CDJ-3000", customer_name="X", phone="555", board_id="820-00239"
    )

    assert store.get_by_ref(ticket.ref).board_id == "820-00239"


def test_board_id_can_be_edited_later(store, ref) -> None:
    store.update_ticket(ref, board_id="820-00239")

    assert store.get_by_ref(ref).board_id == "820-00239"


def test_board_id_can_be_cleared(store, ref) -> None:
    store.update_ticket(ref, board_id="820-00239")
    store.update_ticket(ref, board_id="")

    assert store.get_by_ref(ref).board_id is None


def test_board_id_is_trimmed(store, ref) -> None:
    store.update_ticket(ref, board_id="  820-00239  ")

    assert store.get_by_ref(ref).board_id == "820-00239"


# --- doc links --------------------------------------------------------------


def test_a_ticket_starts_with_no_links(store, ref) -> None:
    assert store.list_links(ref) == []


def test_a_link_can_be_added(store, ref) -> None:
    store.add_link(ref, "Boardview", "http://nas/boards/820-00239.obv")
    links = store.list_links(ref)

    assert len(links) == 1
    assert links[0].label == "Boardview"
    assert links[0].url == "http://nas/boards/820-00239.obv"


def test_multiple_links(store, ref) -> None:
    store.add_link(ref, "Boardview", "http://nas/a.obv")
    store.add_link(ref, "Schematic", "http://nas/a.pdf")

    assert [link.label for link in store.list_links(ref)] == ["Boardview", "Schematic"]


def test_a_link_can_be_removed(store, ref) -> None:
    first = store.add_link(ref, "Boardview", "http://nas/a.obv")
    store.add_link(ref, "Schematic", "http://nas/a.pdf")

    store.remove_link(ref, first.id)

    assert [link.label for link in store.list_links(ref)] == ["Schematic"]


def test_a_url_is_required(store, ref) -> None:
    with pytest.raises(ValueError):
        store.add_link(ref, "Boardview", "")


def test_a_blank_url_is_rejected(store, ref) -> None:
    with pytest.raises(ValueError):
        store.add_link(ref, "Boardview", "   ")


def test_a_label_defaults_when_omitted(store, ref) -> None:
    """A URL with no label is still usable — don't force busywork."""
    link = store.add_link(ref, "", "http://nas/a.obv")

    assert link.label
    assert link.url == "http://nas/a.obv"


def test_links_are_scoped_to_their_ticket(store) -> None:
    a = store.create_ticket(device="A", customer_name="X", phone="555").ref
    b = store.create_ticket(device="B", customer_name="Y", phone="555").ref
    store.add_link(a, "Boardview", "http://nas/a.obv")

    assert len(store.list_links(a)) == 1
    assert store.list_links(b) == []


def test_adding_a_link_to_an_unknown_ticket_raises(store) -> None:
    with pytest.raises(LookupError):
        store.add_link("26-9999", "Boardview", "http://nas/a.obv")


def test_removing_an_unknown_link_is_harmless(store, ref) -> None:
    store.remove_link(ref, 999)  # no such link — a no-op, not an error

    assert store.list_links(ref) == []
