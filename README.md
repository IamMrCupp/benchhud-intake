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

```bash
docker build -t benchhud-intake .
docker run -p 8000:8000 benchhud-intake
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
