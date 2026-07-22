"""The package imports and reports a version.

Thin on purpose — it exists so `pytest` has something to collect before there's
real code, and so a broken `src/` layout fails the suite rather than passing an
empty run.
"""

import intake


def test_version_is_exposed() -> None:
    assert intake.__version__
