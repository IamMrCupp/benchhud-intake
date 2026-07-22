"""Ref formatting, parsing and allocation.

A ref gets read out over the phone and written on tape stuck to a board, so the
formatting tests are about human legibility. The allocation tests are about
never handing the same ref to two different customers.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from intake.db import connect
from intake.refs import allocate_counter, format_ref, parse_ref
from intake.schema import initialise


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def conn(db_path):
    with connect(db_path) as c:
        yield c


# --- formatting -------------------------------------------------------------


def test_formats_with_year_prefix_and_padding() -> None:
    assert format_ref(26, 7) == "26-0007"


def test_first_of_the_year_is_0001() -> None:
    assert format_ref(26, 1) == "26-0001"


def test_pads_to_a_fixed_width() -> None:
    """Fixed width so refs line up on labels and read consistently aloud."""
    assert format_ref(26, 42) == "26-0042"
    assert format_ref(26, 999) == "26-0999"
    assert format_ref(26, 1000) == "26-1000"


def test_counter_past_9999_widens_rather_than_breaking() -> None:
    """Padding is cosmetic. Ticket 10000 widens; it doesn't corrupt the ref."""
    assert format_ref(26, 10000) == "26-10000"
    assert parse_ref("26-10000") == (26, 10000)


@pytest.mark.parametrize(
    ("year", "counter"),
    [(26, 1), (26, 7), (25, 9), (26, 9999), (26, 10000), (99, 1)],
)
def test_parse_round_trips(year, counter) -> None:
    assert parse_ref(format_ref(year, counter)) == (year, counter)


@pytest.mark.parametrize(
    "bad", ["", "26", "260007", "26-", "-0007", "aa-0007", "26-abcd", "26-0007-1", "26-0000"]
)
def test_rejects_malformed_refs(bad) -> None:
    with pytest.raises(ValueError):
        parse_ref(bad)


def test_parse_tolerates_surrounding_whitespace() -> None:
    """Refs get typed by hand and pasted out of messages."""
    assert parse_ref("  26-0007 ") == (26, 7)


# --- allocation -------------------------------------------------------------


def test_first_allocation_of_a_year_is_one(conn) -> None:
    assert allocate_counter(conn, 26) == 1


def test_allocations_increment(conn) -> None:
    assert [allocate_counter(conn, 26) for _ in range(3)] == [1, 2, 3]


def test_counter_resets_each_year(conn) -> None:
    """25-0009 and 26-0001 must coexist — counters are per-year."""
    for _ in range(9):
        allocate_counter(conn, 25)

    assert allocate_counter(conn, 26) == 1


def test_years_are_independent(conn) -> None:
    allocate_counter(conn, 25)
    allocate_counter(conn, 26)

    assert allocate_counter(conn, 25) == 2
    assert allocate_counter(conn, 26) == 2


def test_allocation_is_monotonic_across_deletes(conn) -> None:
    """A deleted ticket must never hand its ref to a different customer.

    This is why allocation reads a counter table instead of MAX(counter)+1 over
    the tickets themselves: refs get spoken aloud and written on tape, and a
    reused one points at the wrong board.
    """
    first = allocate_counter(conn, 26)
    second = allocate_counter(conn, 26)
    conn.execute(
        "INSERT INTO tickets (year, counter, device, symptoms, customer_name, phone,"
        " status, created_at, updated_at)"
        " VALUES (26, ?, 'X1', 'no power', 'A. Customer', '555-0100', 'intake', 't', 't')",
        (second,),
    )
    conn.commit()
    conn.execute("DELETE FROM tickets WHERE counter = ?", (second,))
    conn.commit()

    assert (first, second) == (1, 2)
    assert allocate_counter(conn, 26) == 3


def test_survives_reopening_the_database(db_path) -> None:
    """The counter is durable, not per-connection state."""
    with connect(db_path) as first:
        allocate_counter(first, 26)
        allocate_counter(first, 26)

    with connect(db_path) as second:
        assert allocate_counter(second, 26) == 3


def test_concurrent_allocation_never_duplicates(db_path) -> None:
    """The failure this design exists to prevent: two customers, one ref."""
    workers = 8
    per_worker = 10

    def allocate_many() -> list[int]:
        with connect(db_path) as c:
            return [allocate_counter(c, 26) for _ in range(per_worker)]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [
            n for batch in pool.map(lambda _: allocate_many(), range(workers)) for n in batch
        ]

    assert len(results) == workers * per_worker
    assert len(set(results)) == len(results), "duplicate ref allocated"
    assert sorted(results) == list(range(1, workers * per_worker + 1)), "gap or overlap"
