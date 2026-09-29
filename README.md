# benchhud-intake

Self-hosted intake for a board-repair bench — a tablet-friendly web form for
taking work in, backed by a small REST API that [benchhud](https://github.com/IamMrCupp/benchhud)
consumes as a `TicketSource`.

Runs on the bench LAN. No cloud, no subscription.

## Why it's a separate repo

benchhud composites its panels to a virtual camera for streaming. Customer
names, phone numbers and addresses can't live anywhere near that surface — so
they live here instead, and benchhud stores only a reference (`Job.external_ref`)
pointing back. This service is the system of record for customer data; benchhud
is the bench log.

That split is why this repo is the one holding customer PII, and why it stays
LAN-only and never gets a public ingress.

## Downloads

Every [release](https://github.com/IamMrCupp/benchhud-intake/releases) has its installers
attached: the Linux service `.deb` (amd64 and arm64), the Windows tray-app MSI
and the macOS tray app. They're built by the same CI jobs every PR runs, at the
release tag, including the install checks. The macOS app is ad-hoc signed, so
the first launch needs right-click → Open.

```bash
gh release download --repo IamMrCupp/benchhud-intake --pattern '*_amd64.deb'
```

PRs and merges to `main` build and check only the Linux amd64 packages, since
macOS and Windows runners bill at 10× and 2×. The macOS, Windows and arm64 builds
run for every release, by hand, or on a PR labelled `full-ci` (use it on a PR
that touches packaging). Docs-only changes skip CI.

## The menu-bar app

On a Mac or Windows PC, the easiest way to run intake is the app: it lives in the
menu bar (macOS) or system tray (Windows), with no window of its own.

- **First launch** asks for a password. intake holds customer records, so it
  won't serve without one.
- The menu shows whether it's running and where, with **Open in browser**,
  **Share on the LAN** (so a tablet can reach it), **Start at login**,
  **Set password…**, **New API token…** (copied to the clipboard for benchhud's
  Tickets tab), **Open data folder** and **Quit**.
- It uses the same data folder and `settings.env` as `benchhud-intake serve`,
  so you can switch between the two.

CI builds it on every PR: `benchhud-intake-macos` (a zipped `.app`) and
`benchhud-intake-msi`. Each is launched in a smoke mode that serves and answers
`/healthz` before the job passes. From a `uv tool` install, run it with
`benchhud-intake tray` (install the `tray` extra: `'benchhud-intake[tray] @ git+…'`).

To build the app yourself: `uvx briefcase create macOS app && uvx briefcase build macOS app`
(or `windows`). `scripts/make_icons.py` regenerates the icons.

## On a Linux box, as a service

For a headless bench machine or a Pi: a `.deb` that runs intake under systemd.
CI builds it for `amd64` and `arm64` on every PR (`benchhud-intake-deb-amd64`,
`benchhud-intake-deb-arm64`), for Ubuntu 26.04.

```bash
sudo apt install ./benchhud-intake_<version>_amd64.deb
sudo benchhud-intake set-password         # it won't start without one
sudo benchhud-intake new-token            # optional, for benchhud
sudo systemctl start benchhud-intake      # http://127.0.0.1:8000/
```

- It runs as its own `benchhud-intake` system user, with its data in
  `/var/lib/benchhud-intake`, and it's enabled to start at boot.
- It runs **sandboxed**: no capabilities, a read-only view of the system
  (`ProtectSystem=strict`) with only its data folder writable, its own `/tmp`,
  no devices, TCP and local sockets only, and systemd's `@system-service`
  syscall set. CI checks `systemd-analyze security` stays at or below **4.0**,
  so it can't quietly loosen.
- `settings.env` there stays **root-only**. systemd reads it and passes the
  password and token in, so the service user never can.
- It listens on loopback. For a tablet, add `INTAKE_HOST=0.0.0.0` to
  `settings.env` and `sudo systemctl restart benchhud-intake`.
- With no password it fails once and stays stopped, rather than restarting in
  a loop. `journalctl -u benchhud-intake` says why.
- Removing the package keeps the data and the service user: deleting customer
  records should never be a side effect of `apt remove`.

## Installing it as a program

No cluster needed. Install the command with [uv](https://docs.astral.sh/uv/)
(or pipx), set a password once, and run it:

```bash
uv tool install git+https://github.com/IamMrCupp/benchhud-intake
benchhud-intake set-password     # required: it won't serve customer records without one
benchhud-intake new-token        # optional: the token benchhud's Tickets tab uses
benchhud-intake serve            # http://127.0.0.1:8000/
```

`serve` stays on this machine by default. `--host 0.0.0.0` shares it on the LAN
so a tablet can reach it. It holds customer records, so never on a public network.

Everything lives in one per-user folder. `benchhud-intake paths` shows it:

| OS | Data folder |
|---|---|
| macOS | `~/Library/Application Support/benchhud-intake` |
| Windows | `%APPDATA%\benchhud-intake` |
| Linux | `$XDG_DATA_HOME/benchhud-intake` (`~/.local/share/…`) |

It holds `intake.db`, `uploads/` and `settings.env`, the owner-only settings
file that `set-password` and `new-token` write. `--data-dir` or
`INTAKE_DATA_DIR` points it elsewhere. **Environment variables always win over
the settings file**, so the container and the cluster deployment below work
exactly as before.

## Running it locally

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via
`.python-version` — uv will fetch it if you don't have it.

```bash
uv sync
uv run uvicorn intake.app:app --reload
```

Then hit http://127.0.0.1:8000/ for the app, or `/healthz` for the probe.

## Development

```bash
uv run pytest                  # tests
uv run ruff check .            # lint
uv run ruff format .           # format
```

CI runs all three on every PR, plus a multi-arch container build. Both must be
green before merge.

The lint config is kept deliberately identical to benchhud's, so switching
between the two repos costs nothing.

## Container

Images are on ghcr.io for `linux/amd64` and `linux/arm64`: `:latest` tracks
`main`, and each release is tagged `:X.Y.Z` and `:X.Y` so a deploy can pin one.

With Docker Compose (or `podman compose`), using the `compose.yaml` here:

```bash
docker compose run --rm intake benchhud-intake set-password   # once
docker compose run --rm intake benchhud-intake new-token      # optional, for benchhud
docker compose up -d                                          # http://127.0.0.1:8000/
```

The data folder in the image is `/data` (`INTAKE_DATA_DIR`); compose mounts a
named volume there, so the database, photos and `settings.env` survive
upgrades. The container starts with `benchhud-intake serve`, which reads
`settings.env` itself, so the `$` in the password hash is never touched by
Compose's variable expansion. Environment variables still win, which is how the
cluster passes its Secret.

To build it yourself:

```bash
docker build -t benchhud-intake .
```

The image is built for `linux/amd64` **and `linux/arm64`** — the deploy target is
a Raspberry Pi cluster, and an arm64-only mistake is invisible until the pod
refuses to schedule.

## Deployment

**To run it on your own Kubernetes**, use the kustomize base in
[`deploy/kubernetes/`](deploy/kubernetes/README.md) from an overlay. It sets the
two things that must not change (one replica, Recreate) and leaves namespace,
storage and exposure to you.

The maintainer's own cluster runs from `cluster-management` (Flux GitOps),
alongside the other apps, following the same pattern as `apps/recipe-box`.
Merging to `main` here pushes
`ghcr.io/iammrcupp/benchhud-intake:latest`; Flux picks up the new digest and
rolls it out.

Two constraints that are easy to get wrong and painful to debug:

- **SQLite lives on an NFS volume**, so the app runs with `journal_mode=TRUNCATE`
  and `synchronous=FULL` — **not WAL**, which misbehaves on NFS and fails by
  corrupting rather than erroring.
- **Exactly one writer.** The Deployment must stay at `replicas: 1` with
  `strategy: Recreate`.

## License

Source-available under the [PolyForm Small Business License 1.0.0](https://polyformproject.org/licenses/small-business/1.0.0/)
— see [LICENSE](LICENSE). Free to use, modify and distribute if your company has
fewer than 100 people and under $1M in annual revenue, which covers personal use
and most small shops. Above that, a commercial licence is required — get in touch.

Note this is *source-available*, not open source: the code is readable and
freely usable within those limits, but it isn't an OSI-approved licence.
