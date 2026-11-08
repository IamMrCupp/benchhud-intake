"""The .deb postinst and the 8000 → 8401 default-port move.

A fresh install records 8401; an upgrade from before the move keeps 8000, so an
existing bench (and the benchhud config pointing at it) keeps working. The
script runs with its system commands stubbed and its settings path redirected.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from intake import settings

POSTINST = Path(__file__).resolve().parents[1] / "packaging" / "deb" / "postinst"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="POSIX shell script"
)


def _run(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run postinst against ``tmp_path/settings.env`` with no-op system commands."""
    settings_file = tmp_path / "settings.env"
    script = POSTINST.read_text().replace(
        "SETTINGS=/var/lib/benchhud-intake/settings.env", f"SETTINGS={settings_file}"
    )
    assert f"SETTINGS={settings_file}" in script
    (tmp_path / "postinst").write_text(script)
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    for name in ("getent", "useradd", "install", "systemctl"):
        stub = stubs / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["sh", str(tmp_path / "postinst"), *argv],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _port(tmp_path: Path) -> str | None:
    return settings.read_settings(tmp_path / "settings.env").get("INTAKE_PORT")


def test_a_fresh_install_records_8401(tmp_path) -> None:
    result = _run(tmp_path, "configure")
    assert _port(tmp_path) == "8401"
    assert "127.0.0.1:8401" in result.stdout


def test_an_upgrade_from_before_the_move_keeps_8000(tmp_path) -> None:
    settings.write_setting(tmp_path / "settings.env", "INTAKE_PASSWORD_HASH", "scrypt$x$y")
    result = _run(tmp_path, "configure", "0.2.2")
    assert _port(tmp_path) == "8000"
    # The other settings survive, and the owner hears why.
    assert settings.read_settings(tmp_path / "settings.env")["INTAKE_PASSWORD_HASH"] == (
        "scrypt$x$y"
    )
    assert "8000" in result.stderr


def test_an_upgrade_without_a_settings_file_keeps_8000(tmp_path) -> None:
    _run(tmp_path, "configure", "0.2.2")
    assert _port(tmp_path) == "8000"


def test_a_file_without_a_final_newline_stays_parseable(tmp_path) -> None:
    (tmp_path / "settings.env").write_text("INTAKE_HOST=0.0.0.0")
    _run(tmp_path, "configure", "0.2.2")
    values = settings.read_settings(tmp_path / "settings.env")
    assert values == {"INTAKE_HOST": "0.0.0.0", "INTAKE_PORT": "8000"}


@pytest.mark.parametrize("port", ["8000", "8401", "9123"])
def test_a_port_already_set_is_left_alone(tmp_path, port) -> None:
    settings.write_setting(tmp_path / "settings.env", "INTAKE_PORT", port)
    _run(tmp_path, "configure", "0.3.0")
    assert _port(tmp_path) == port
    assert (tmp_path / "settings.env").read_text().count("INTAKE_PORT") == 1


def test_later_upgrades_of_a_fresh_install_stay_on_8401(tmp_path) -> None:
    _run(tmp_path, "configure")
    _run(tmp_path, "configure", "0.3.0")
    assert _port(tmp_path) == "8401"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_settings_file_it_creates_is_owner_only(tmp_path) -> None:
    _run(tmp_path, "configure")
    assert (tmp_path / "settings.env").stat().st_mode & 0o077 == 0
