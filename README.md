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

**That still holds now that the public site has a request form.** Requests come
in through a write-only API over the private network: a relay on the site's
side forwards them, with a token that can add a request and read nothing.
Nothing in this service is reachable from the internet, and nothing new is
readable by the thing that is. The next section has the details.

## Repair requests from the public site

The shop's public site has a "request a repair" form. The request lands here,
and this service still isn't on the internet. A small relay that lives with the
site takes the form and forwards it over the private network:

```
browser → public relay → (private network, submit token) → POST /api/requests
          (the site's repo)                                  (this service)
```

`POST /api/requests` is **write-only**. It takes JSON, answers `202` with an
opaque id, and says nothing else — no ref, no echo of what was sent. A request
isn't a ticket: it gets no `YY-NNNN` ref and never shows up in `/api/tickets`
until someone at the bench accepts it.

The relay authenticates with its own token, sent as
`Authorization: Bearer <token>`:

```bash
benchhud-intake new-submit-token          # saved as INTAKE_SUBMIT_TOKEN
benchhud-intake new-submit-token --print  # the token alone on stdout
```

That token opens this one route. It gets a `401` on `/api/tickets` and
everything else, so a relay that's been broken into can add to a queue you
review and can't read a customer record. With no `INTAKE_SUBMIT_TOKEN` set the
route answers `401` to everyone. Don't reuse the API token for it — set both to
the same value and the submit token stops working, on purpose.

Two backstops don't depend on the relay behaving. Past
`INTAKE_MAX_PENDING_REQUESTS` unreviewed requests (default 200) the route
answers `429` and stores nothing. And a relay that sends an `Idempotency-Key`
header can retry safely: the same key gets the same answer and no second
request.

### Reviewing them

Signed in, **Requests** sits next to Tickets, with a count beside it whenever
something is waiting. Each request has two buttons:

- **Accept** makes a real ticket and takes you to it. This is when the ref is
  allocated, so junk can't burn `YY-NNNN` numbers. The ticket starts at
  `awaiting_arrival`, for drop-off and mail-in alike: saying yes doesn't put
  the device on the bench. The request keeps a note of which ticket it became
  and drops its own copy of the customer's details.
- **Dismiss** sets it aside. No ticket, no ref.

Dismissed requests are the details of people who never became customers, so
they don't stay. Thirty days after a request is dismissed (or accepted) it's
deleted; `INTAKE_REQUEST_RETENTION_DAYS` changes the window. There's no
scheduler: the purge runs when a signed-in page loads or a new request
arrives. A request that's still waiting is never purged.

### Getting told about one

The count in the nav only helps if you open the app. For a request that comes
in overnight, set `INTAKE_NOTIFY_URL` and each new request POSTs one line to
it:

```
benchhud-intake: new repair request (2 waiting)
```

That's all it ever says. **No name, no device, no request id** — a
notification service is someone else's computer and your phone's lock screen,
and the details stay behind the login. It's the only outbound call this
service makes, and it's off until you set the URL.

`INTAKE_NOTIFY_FORMAT` picks the body: `json` (the default) sends
`{"text": …, "content": …, "pending": N}`, which is the shape Slack- and
Discord-style webhooks and Home Assistant read; `text` sends the bare line,
which is what [ntfy](https://ntfy.sh/) wants. The URL is usually the secret, so
keep it with the tokens (`settings.env` or the Kubernetes Secret). The ping
goes out after the relay has its answer, times out after five seconds, and a
failure costs a buzz, never a request.

### The contract

**The contract is a file: [`tests/fixtures/request-contract.json`](tests/fixtures/request-contract.json).**
Field names, required and optional, length caps, the `handoff` values, the
headers, and the exact body for `202`, `400`, `401`, `413`, `415` and `429`,
with an example of each. `tests/test_request_contract.py` replays every example
against the real endpoint, so the file can't drift from the code. If you're
building the other end, copy it rather than guess.

## Downloads

Every [release](https://github.com/IamMrCupp/benchhud-intake/releases) has its installers
attached: the Linux service `.deb` (amd64 and arm64), the Windows tray-app MSI
and the macOS tray app. They're built by the same CI jobs every PR runs, at the
release tag, including the install checks. The macOS app is a `.dmg` for Apple
silicon, signed with a Developer ID and notarized, so it opens without a
Gatekeeper warning.

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

Download it from the latest [release](https://github.com/IamMrCupp/benchhud-intake/releases):
the `.dmg` (macOS, Apple silicon, signed and notarized) or the `.msi`
(Windows). Both are built for every release (and on a PR labelled
`full-ci`), and launched in a smoke mode that serves and answers `/healthz`
before they're attached. From a `uv tool` install, run it with
`benchhud-intake tray` (install the `tray` extra: `'benchhud-intake[tray] @ git+…'`).

On a Mac, [Homebrew](https://github.com/IamMrCupp/homebrew-bench) also installs it, and keeps it current with
`brew upgrade`:

```sh
brew tap iammrcupp/bench
brew install --cask benchhud-intake
```

**The whole bench on a Mac (optional).** If the same Mac runs the rest of the
bench, the tap's `bench-suite` cask installs benchhud, both menu-bar apps and
`bench-setup`:

```sh
brew tap iammrcupp/bench
brew trust --tap iammrcupp/bench
brew install --cask bench-suite
bench-setup
```

`bench-setup` sets one password for both apps, makes a token each, and writes
the URLs and tokens into benchhud's config, so there's no token to copy by
hand. macOS asks before the first launch of a download, so it will tell you to
open benchhud intake once from Applications, then run it again. Nothing here needs it.
See [bench-suite](https://github.com/IamMrCupp/bench-suite#on-a-mac).

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
sudo systemctl start benchhud-intake      # http://127.0.0.1:8401/
```

**Or from the bench apt repo (optional).** The same package is in the signed
apt repo that [bench-suite](https://github.com/IamMrCupp/bench-suite#readme)
publishes, so updates arrive with `apt upgrade`:

```bash
sudo curl -fsSLo /usr/share/keyrings/bench-archive-keyring.gpg https://apt.yolorepair.com/bench-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/bench-archive-keyring.gpg] https://apt.yolorepair.com stable main" \
  | sudo tee /etc/apt/sources.list.d/bench.list
sudo apt update
sudo apt install benchhud-intake
```

If the same machine runs the rest of the bench, `sudo apt install bench-suite`
installs the lot, and `sudo bench-setup` does the setup above for every service
at once: one password, a token each, and the URLs and tokens written into
benchhud's config. `bench-setup doctor` checks the result. Nothing here needs it.

- It runs as its own `benchhud-intake` system user, with its data in
  `/var/lib/benchhud-intake`, and it's enabled to start at boot.
- It runs **sandboxed**: no capabilities, a read-only view of the system
  (`ProtectSystem=strict`) with only its data folder writable, its own `/tmp`,
  no devices, TCP and local sockets only, and systemd's `@system-service`
  syscall set. CI checks `systemd-analyze security` stays at or below **4.0**,
  so it can't quietly loosen.
- It listens on **8401** (bench-suite gives intake, bench-parts and bench-recon
  8401–8403 so they can share a host). Older installs keep 8000: the
  upgrade writes `INTAKE_PORT=8000` into `settings.env` when no port is set
  there. Change that line and restart to move it.
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
benchhud-intake serve            # http://127.0.0.1:8401/
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

Every page has a **Source** link in the footer, sign-in page included.
`INTAKE_SOURCE_URL` sets where it points and defaults to this repository. If you
run a modified copy for other people, point it at your own code — that's the
offer of source AGPL section 13 asks for. It wants an `http://` or `https://`
address; anything else is ignored and the link stays on this repository.

For scripts (such as `bench-setup`), both commands have non-interactive forms
that keep secrets out of the command line:

```bash
printf '%s\n' "$PW" | benchhud-intake set-password --stdin   # one line, no confirm
benchhud-intake set-password --generate   # random password, printed once on stdout
benchhud-intake new-token --print         # the token alone on stdout
```

## Looks and themes

Same look as benchhud and burnwright — same palette, same panels — so the
counter tablet and the bench screen read as one set of tools. **Appearance**,
at the foot of every page, picks System, Light or Dark, and the choice sticks
in that browser.

Want your own? Drop a burnwright theme file into the `themes` folder in the
data folder (`benchhud-intake paths` shows where), or point
`INTAKE_THEMES_DIR` at a folder of them. It shows up in the Appearance list on
the next page load. The format is burnwright's, so a theme you've already
written for it works here unchanged:

```json
{
  "name": "Amber terminal",
  "base": "dark",
  "colors": { "bg": "#120d02", "panel": "#1b1404", "fg": "#ffcf70", "accent": "#ffb000" }
}
```

`base` is `"light"` or `"dark"`; anything you leave out comes from it. Colours
only — hex, `rgb()` or `hsl()` — and the names are burnwright's (`bg`, `panel`,
`border`, `fg`, `muted`, `control-bg`, `control-border`, `control-hover`,
`accent`, `accent-soft`, `danger`, `danger-soft`, `warning`, `warning-soft`,
`warning-border`, `ok`, `ok-soft`). A file that can't be used is named at the
foot of the page, to anyone signed in, with the reason.

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
docker compose up -d                                          # http://127.0.0.1:8401/
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

### Behind a reverse proxy

Put TLS in front of it and two settings go with the proxy. Neither is on by
default.

- **`INTAKE_COOKIE_SECURE=1`** once every browser reaches it over HTTPS. Over
  plain HTTP it stops sign-in from working at all.
- **`INTAKE_TRUSTED_PROXIES`**: the proxy's address, or the range it lives in,
  comma-separated. `10.42.0.0/16` and `192.168.7.206` are both fine.

Without the second one, every browser looks like the proxy. Failed logins are
counted per address, so five wrong passwords from any device lock sign-in for
everyone for five minutes. With it, the server believes the proxy's
`X-Forwarded-For` and `X-Forwarded-Proto`, each browser gets its own count, and
the links in API responses say `https`.

Keep the list as narrow as you can. Anything inside it can claim to be any
address, which means it can pick its own lockout bucket. On Kubernetes that is
usually the ingress controller's pod range, not the whole LAN. A header from
anywhere outside the list is ignored, as it always was.

`serve` refuses to start on `*`, on a range that covers every address, and on
an entry it can't read. A typo here should not quietly mean "trust nothing".

## License

Open source under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html)
or later — see [LICENSE](LICENSE). Use it, change it, ship it. If you distribute
a modified build, or run one as a service other people use, your changes go out
under the same licence.

Copyright © 2026 Aaron Cupp. If the AGPL doesn't fit how you want to use it, a
commercial licence is available — get in touch.
