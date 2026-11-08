"""The ``benchhud-intake`` command: run it, and set it up, without a cluster.

    benchhud-intake set-password     # once: the login password (required)
    benchhud-intake new-token        # optional: the API token benchhud uses
    benchhud-intake serve            # http://127.0.0.1:8401/
    benchhud-intake paths            # where the data and settings live

Everything lands in the per-OS data folder (see :mod:`intake.settings`), and
environment variables still override it, so the container is unaffected.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from intake import auth, settings

#: Addresses that keep the server on this machine.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _data(args: argparse.Namespace) -> Path:
    return Path(args.data_dir).expanduser() if args.data_dir else settings.data_dir()


def cmd_serve(args: argparse.Namespace, run: Callable[..., None] | None = None) -> int:
    data = _data(args)
    data.mkdir(parents=True, exist_ok=True)
    settings.apply(data)
    if not os.environ.get(auth.ENV_VAR, "").strip():
        # Fail closed, as the app itself does: no password, no customer records.
        print(
            "benchhud-intake has no password yet, and won't serve customer records "
            "without one. Set it with:\n    benchhud-intake set-password",
            file=sys.stderr,
        )
        return 2
    if args.host not in _LOOPBACK:
        print(
            f"Listening on {args.host}: anyone on this network can reach the login "
            "page. This service holds customer records; keep it off public networks.",
            file=sys.stderr,
        )
    from intake.app import app  # imported late: it reads the settings at import

    if run is None:
        import uvicorn

        run = uvicorn.run
    print(f"benchhud-intake on http://{args.host}:{args.port}/  (data: {data})")
    run(app, host=args.host, port=args.port)
    return 0


def cmd_set_password(args: argparse.Namespace, ask: Callable[[str], str] = getpass.getpass) -> int:
    password = ask("New intake password: ")
    if password != ask("Again: "):
        print("The passwords didn't match.", file=sys.stderr)
        return 1
    if not password.strip():
        print("The password can't be blank.", file=sys.stderr)
        return 1
    path = settings.settings_path(_data(args))
    settings.write_setting(path, auth.ENV_VAR, auth.hash_password(password))
    print(f"Saved to {path}. Restart benchhud-intake if it's running.")
    return 0


def cmd_new_token(args: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(32)
    path = settings.settings_path(_data(args))
    settings.write_setting(path, auth.API_TOKEN_ENV_VAR, token)
    print(token)
    print(
        f"Saved to {path}. Paste it into benchhud: Preferences → Tickets → Token. "
        "The old token, if any, stops working when benchhud-intake restarts.",
        file=sys.stderr,
    )
    return 0


def cmd_tray(args: argparse.Namespace) -> int:
    if args.data_dir:
        os.environ[settings.DATA_DIR_ENV] = str(Path(args.data_dir).expanduser())
    try:
        from intake.tray import main as tray_main
    except ImportError:
        print(
            "The tray app needs PySide6. Install the tray extra:\n"
            "    uv tool install 'benchhud-intake[tray] @ "
            "git+https://github.com/IamMrCupp/benchhud-intake'",
            file=sys.stderr,
        )
        return 2
    return tray_main()


def cmd_paths(args: argparse.Namespace) -> int:
    data = _data(args)
    print(f"data:     {data}")
    print(f"settings: {settings.settings_path(data)}")
    print(f"database: {os.environ.get(settings.DB_PATH_ENV) or data / 'intake.db'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchhud-intake",
        description="Self-hosted repair intake. Holds customer records; keep it on your LAN.",
    )
    parser.add_argument("--data-dir", help="data folder (default: per-user app data)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share on the LAN")
    serve.add_argument("--port", type=int, default=8401, help="default 8401")
    serve.set_defaults(func=cmd_serve)
    sub.add_parser("set-password", help="set the login password").set_defaults(
        func=cmd_set_password
    )
    sub.add_parser("new-token", help="make the API token benchhud uses").set_defaults(
        func=cmd_new_token
    )
    sub.add_parser("paths", help="show where data and settings live").set_defaults(func=cmd_paths)
    sub.add_parser("tray", help="run as a menu-bar / tray app").set_defaults(func=cmd_tray)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
