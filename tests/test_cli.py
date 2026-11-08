"""Running intake without a cluster (#83): the data folder, the settings file, the CLI."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from intake import auth, cli, settings

# --- the data folder ------------------------------------------------------------


def test_data_folder_follows_each_platforms_convention(tmp_path) -> None:
    home = tmp_path
    assert settings.data_dir({}, "darwin", home) == (
        home / "Library" / "Application Support" / "benchhud-intake"
    )
    assert settings.data_dir({"APPDATA": "C:/Users/a/AppData/Roaming"}, "win32", home) == Path(
        "C:/Users/a/AppData/Roaming/benchhud-intake"
    )
    assert settings.data_dir({}, "linux", home) == home / ".local" / "share" / "benchhud-intake"
    assert settings.data_dir({"XDG_DATA_HOME": "/srv/x"}, "linux", home) == Path(
        "/srv/x/benchhud-intake"
    )


def test_data_folder_can_be_pointed_anywhere(tmp_path) -> None:
    env = {"INTAKE_DATA_DIR": str(tmp_path / "bench")}
    assert settings.data_dir(env, "darwin") == tmp_path / "bench"


# --- the settings file ------------------------------------------------------------


def test_settings_keep_other_keys_and_take_values_literally(tmp_path) -> None:
    path = tmp_path / "settings.env"
    settings.write_setting(path, "INTAKE_PASSWORD_HASH", "scrypt$16384$8$1$ab$cd")
    settings.write_setting(path, "INTAKE_API_TOKEN", "tok")
    assert settings.read_settings(path) == {
        "INTAKE_PASSWORD_HASH": "scrypt$16384$8$1$ab$cd",  # the $s survive
        "INTAKE_API_TOKEN": "tok",
    }


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_an_unreadable_settings_file_counts_as_empty(tmp_path, monkeypatch) -> None:
    """The .deb service: systemd passes the root-owned file's values in itself."""
    path = tmp_path / "settings.env"
    settings.write_setting(path, "INTAKE_API_TOKEN", "tok")

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(path), "read_text", denied)
    assert settings.read_settings(path) == {}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_settings_file_is_owner_only(tmp_path) -> None:
    path = tmp_path / "settings.env"
    settings.write_setting(path, "INTAKE_API_TOKEN", "tok")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_environment_variables_win_over_the_file(tmp_path) -> None:
    settings.write_setting(settings.settings_path(tmp_path), "INTAKE_API_TOKEN", "from-file")
    env = {"INTAKE_API_TOKEN": "from-env"}
    settings.apply(tmp_path, env)
    assert env["INTAKE_API_TOKEN"] == "from-env"


def test_apply_fills_in_the_gaps(tmp_path) -> None:
    settings.write_setting(settings.settings_path(tmp_path), "INTAKE_API_TOKEN", "from-file")
    env: dict[str, str] = {}
    settings.apply(tmp_path, env)
    assert env["INTAKE_API_TOKEN"] == "from-file"
    assert env["INTAKE_DB_PATH"] == str(tmp_path / "intake.db")
    assert env["INTAKE_UPLOADS_PATH"] == str(tmp_path / "uploads")


# --- the commands --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _own_environment(monkeypatch):
    """``serve`` fills in os.environ; give each test its own copy so nothing leaks."""
    monkeypatch.setattr(os, "environ", dict(os.environ))


def _answers(*replies: str):
    it = iter(replies)
    return lambda _prompt: next(it)


def _args(tmp_path, *argv: str):
    return cli.build_parser().parse_args(["--data-dir", str(tmp_path), *argv])


def test_set_password_stores_a_hash_that_verifies(tmp_path) -> None:
    code = cli.cmd_set_password(_args(tmp_path, "set-password"), _answers("pw one", "pw one"))
    assert code == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password("pw one", stored)
    assert "pw one" not in (tmp_path / "settings.env").read_text()


def test_set_password_refuses_a_mismatch_or_a_blank(tmp_path) -> None:
    args = _args(tmp_path, "set-password")
    assert cli.cmd_set_password(args, _answers("a", "b")) == 1
    assert cli.cmd_set_password(args, _answers("  ", "  ")) == 1
    assert not (tmp_path / "settings.env").exists()


def test_new_token_is_saved_and_printed(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "new-token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 40
    assert settings.read_settings(tmp_path / "settings.env")[auth.API_TOKEN_ENV_VAR] == token


def test_serve_refuses_without_a_password(tmp_path, monkeypatch, capsys) -> None:
    """Fail closed, as the app does: never serve customer records unprotected."""
    os.environ.pop(auth.ENV_VAR, None)
    ran = []
    code = cli.cmd_serve(_args(tmp_path, "serve"), run=lambda *a, **k: ran.append(a))
    assert code == 2
    assert ran == []
    assert "set-password" in capsys.readouterr().err


def test_serve_uses_the_data_folder_and_stays_on_loopback(tmp_path, monkeypatch, capsys) -> None:
    for var in ("INTAKE_DB_PATH", "INTAKE_UPLOADS_PATH"):
        os.environ.pop(var, None)
    calls = []
    code = cli.cmd_serve(
        _args(tmp_path, "serve", "--port", "8123"), run=lambda app, **kw: calls.append(kw)
    )
    assert code == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123}]
    assert os.environ["INTAKE_DB_PATH"] == str(tmp_path / "intake.db")
    assert "anyone on this network" not in capsys.readouterr().err


def test_serve_warns_when_shared_on_the_lan(tmp_path, capsys) -> None:
    cli.cmd_serve(_args(tmp_path, "serve", "--host", "0.0.0.0"), run=lambda *a, **k: None)
    assert "anyone on this network" in capsys.readouterr().err


def test_paths_names_the_settings_file(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "paths"]) == 0
    assert str(tmp_path / "settings.env") in capsys.readouterr().out


# --- non-interactive flags, for bench-setup ---------------------------------------


def _stdin(monkeypatch, text: str) -> None:
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _never_ask(_prompt: str) -> str:
    raise AssertionError("a non-interactive flag must not prompt")


def test_set_password_stdin_reads_one_line_without_confirming(tmp_path, monkeypatch, capsys):
    _stdin(monkeypatch, "pw from pipe\n")
    code = cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask)
    assert code == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password("pw from pipe", stored)
    out = capsys.readouterr()
    assert out.out == ""  # stdout stays clean for scripts
    assert "pw from pipe" not in out.err


def test_set_password_stdin_strips_only_the_line_ending(tmp_path, monkeypatch) -> None:
    _stdin(monkeypatch, " spaced pw \r\n")
    assert cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask) == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password(" spaced pw ", stored)


@pytest.mark.parametrize("text", ["", "\n", "   \n"])
def test_set_password_stdin_refuses_a_blank(tmp_path, monkeypatch, text) -> None:
    _stdin(monkeypatch, text)
    assert cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask) == 1
    assert not (tmp_path / "settings.env").exists()


def test_set_password_generate_prints_the_password_once(tmp_path, capsys) -> None:
    code = cli.cmd_set_password(_args(tmp_path, "set-password", "--generate"), _never_ask)
    assert code == 0
    out = capsys.readouterr()
    password = out.out.strip()
    assert out.out == password + "\n"
    assert len(password) >= 20
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password(password, stored)
    assert password not in out.err


def test_set_password_flags_are_mutually_exclusive(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        _args(tmp_path, "set-password", "--stdin", "--generate")
    assert exc.value.code == 2


def test_new_token_print_writes_and_prints_only_the_token(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "new-token", "--print"]) == 0
    out = capsys.readouterr()
    token = out.out.strip()
    assert out.out == token + "\n"
    assert out.err == ""
    assert settings.read_settings(tmp_path / "settings.env")[auth.API_TOKEN_ENV_VAR] == token


def test_new_token_without_print_keeps_its_hint(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "new-token"]) == 0
    assert "Preferences" in capsys.readouterr().err
