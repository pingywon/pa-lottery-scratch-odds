# PA Lottery Scratch Odds

A sortable, searchable board of every active Pennsylvania Lottery scratch-off
game — price, odds, prize tiers, and how many top prizes are actually still
out there — using the real official ticket artwork.

**Unofficial. Not affiliated with, endorsed by, or connected to the
Pennsylvania Lottery in any way.** This is a personal data-transparency
project built entirely on public pages at palottery.pa.gov.

## What this is

A dense, sortable **table** — one row per active game, small ticket
thumbnail, click any column header to sort, click a row to expand the full
prize-tier breakdown. Deliberately not a card grid (an earlier version was;
it was rejected as too cluttered). Columns:

- **Price** — ticket cost.
- **Total Game Odds** — the official published odds of winning *any* prize
  (e.g. "1 in 3.63"), as PA Lottery prints it.
- **Correct Odds** — recalculated *right now* using the game's *exact*
  original ticket count (pulled from its official Pennsylvania Bulletin
  regulatory filing) and the *live* remaining count, for the 6 prize tiers
  PA Lottery actually tracks in real time. An exact calculation, not an
  estimate — but honestly scoped to just those 6 tiers, since PA Lottery
  doesn't publish live remaining counts for every smaller/common prize.
- **Top Prize** — the top prize amount.
- **Winners Left** — top-prize remaining vs. original (e.g. "5 of 5"), with
  a bar that fills up as prizes get depleted (green ≥51% remaining, yellow
  26–50%, red ≤25%).
- **Value Score** — expected payout per ticket, right now, as a % of price.
  The single "best overall pick" ranking (default sort): higher = more of
  your money you'd expect back on average. Built from the *complete* official
  prize-tier table (every tier, not just the top 6), weighted by today's live
  remaining counts wherever PA Lottery tracks one, falling back to each
  tier's original day-one count where there's no live figure (documented
  assumption — can only overstate value slightly, never understate it; see
  `scrape.py`'s `build_game_record()` for the exact formula). Cross-checked
  against a game's own official Bulletin-stated payout percentage on a
  freshly-launched game (Ca$h Money) — computed 80.72% vs. the state's own
  80.12%, a ~0.6-point difference for a game where almost nothing had been
  claimed yet, which is the expected order of agreement.

Expanding a row shows the complete official prize-tier table, the game's
actual ticket artwork front/back, and links to the official PA Lottery page
and Bulletin filing.

Data only updates when someone clicks **Refresh Data** in the app — see
"Refresh schedule" below.

## Quick start

```bash
python3 scrape.py   # populates data.json and images/ (takes a few minutes; ~140 requests)
python3 server.py   # serves the app
```

Then open `http://<this-machine>:8789/`.

## Config (env vars)

| Var        | Default            | Purpose                                                  |
|------------|--------------------|----------------------------------------------------------|
| `PORT`     | `8789`             | HTTP port for `server.py`                                  |
| `BIND`     | `0.0.0.0`          | Bind address for `server.py`                               |
| `DATA_DIR` | the repo directory | Where `data.json`, `images/`, and the scrape lock live     |

`DATA_DIR` exists for the container build: the image ships a snapshot of
`data.json` + `images/` so it runs standalone, but pointing `DATA_DIR` at a
mounted host directory makes it serve live data instead of that snapshot.

## Docker

Published as [`pingywon/pa-lottery-scratch-odds`](https://hub.docker.com/r/pingywon/pa-lottery-scratch-odds).

Standalone, with the snapshot baked into the image:

```bash
docker run -d -p 8789:80 pingywon/pa-lottery-scratch-odds:latest
```

Serving live data from a host checkout instead:

```bash
docker run -d -p 8789:80 \
    -e DATA_DIR=/data -v /path/to/pa-lottery-scratch-odds:/data \
    pingywon/pa-lottery-scratch-odds:latest
```

### On its own LAN IP

`deploy/run.sh` puts the container on the real LAN via a macvlan network, so it
answers on port 80 at its own address rather than sharing the host's ports:

```bash
./deploy/run.sh v1.7.0        # -> http://192.168.13.16/
```

It restarts with `unless-stopped`, so it comes back on its own after a reboot.

One macvlan quirk worth knowing: a macvlan container is unreachable *from the
host that runs it* (though every other machine on the LAN reaches it fine).
`deploy/docker-lan-macvlan-shim.service` + `deploy/macvlan-shim-up.sh` fix that
with a host-side shim interface and a `/32` route per container IP. Install to
`/etc/systemd/system/` and `/usr/local/sbin/` respectively, list the container
IPs in `/etc/default/macvlan-shim`, and enable the unit. Only ever route `/32`s
through the shim — routing the whole subnet shadows the host's own LAN route.

### Cutting a release

```bash
./deploy/release.sh v1.7.0
```

Builds, smoke-tests the image (health endpoint reports a non-zero game count and
the page renders), pushes `:v1.7.0` and `:latest` to Docker Hub, git-tags, and
redeploys. Nothing is pushed if the smoke test fails.

Note that `images/` is gitignored, so a fresh clone must run `python3 scrape.py`
once before it can build an image.

## Endpoints

- `GET /` — the app (single-page, vanilla JS/CSS, no build step)
- `GET /api/games.json` — the current scraped dataset, verbatim
- `POST /api/refresh` — starts a scrape in the background (202 if started, 409 if one's already running); the UI's "Refresh Data" button calls this
- `GET /api/refresh-status` — `{"running": bool}`, polled by the UI while a refresh is in progress
- `GET /images/<path>` — locally-cached ticket art
- `GET /health` — `{"ok": true, "games": N, "scraped_at": "..."}`

## How the scraper works

`scrape.py` is a single stdlib-only Python file (no pip installs), politely
rate-limited (~0.75s between requests, identifying User-Agent), that:

1. Fetches `Active-Games.aspx` — the full active roster in one request.
2. Fetches each game's detail page (`View-Scratch-Off.aspx?id=N`) for price,
   published overall odds, the top prize's original/remaining counts, the
   live remaining counts for the 6 tracked prize tiers, and the two ticket
   image URLs.
3. Fetches each game's official Pennsylvania Bulletin regulatory filing
   (linked from the detail page) for the *exact* original total tickets
   printed and the complete official prize-tier table (every tier, not just
   the top 6).
4. Downloads and locally caches both ticket images per game (skips
   re-downloading art that's already on disk — ticket art never changes for
   a given game).
5. Computes the adjusted-odds figures and a deal score, then writes
   `data.json`.

The scraper degrades gracefully: a failed per-game fetch keeps that game's
previous cached data rather than dropping it, and the whole run refuses to
overwrite `data.json` if fewer than half of the previous run's games
succeeded (guards against silently shipping a broken scrape if PA Lottery's
markup changes).

### A data-honesty note on "original" counts

The Bulletin filing states an *approximate* planned print run before the
game launches. For a game's very top prize tier this figure reliably matches
reality (it's a small, fixed, heavily-marketed number). For higher-volume
tiers lower in the top 6, PA Lottery's live "remaining" count can end up
*higher* than the Bulletin's original approximate count — production runs
for common prize tiers can end up larger than the pre-launch estimate. When
that happens, the app flags that tier's "original" figure (`*`) rather than
hiding or fabricating a number. **The adjusted-odds calculation is
unaffected by this** — it only ever uses the official total tickets printed
and the live remaining count directly, never the potentially-understated
per-tier original.

## Running it as a service

Unit files are in `deploy/` (reference copies — install them to
`/etc/systemd/system/` to actually run):

```bash
sudo cp deploy/pa-lottery-scratch-odds.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pa-lottery-scratch-odds.service
```

Run `scrape.py` manually once before first starting the web service, so
`data.json` exists — the server tolerates a missing `data.json` (shows an
empty board) but there's no reason to start it empty.

`deploy/pa-lottery-scratch-odds-scrape.service` + `.timer` (a daily
06:30-ish auto-refresh) also exist in `deploy/` but are **not installed by
design** — refreshing is on-demand only, via the app's own "Refresh Data"
button (`POST /api/refresh`). If you ever want the daily timer back:
`sudo cp deploy/pa-lottery-scratch-odds-scrape.service deploy/pa-lottery-scratch-odds-scrape.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now pa-lottery-scratch-odds-scrape.timer`.

## Refresh schedule

**On-demand, or automatically when PA Lottery actually updates** — there's no
blind scheduled re-scrape. Data refreshes when: someone clicks "Refresh Data"
in the app, `POST /api/refresh` is called directly, or the watchdog (below)
detects PA Lottery has genuinely posted new numbers. A `scrape.lock` PID file
(managed by `scrape.py`) guards against two scrapes running at once, no
matter which of these triggered it. The server re-reads `data.json` from
disk on every request, so a fresh scrape shows up on the very next page
load — no restart needed.

## Watchdog (`watchdog.py`)

PA Lottery only updates their own "wins remaining" figures irregularly —
observed no change at all for 5+ days straight. Rather than re-scrape all
~140 pages on a fixed schedule (which would just refetch identical data most
of the time), `watchdog.py` checks *one* lightweight page — the freshness
stamp on `prizes-remaining.aspx` — every 15 minutes via
`pa-lottery-scratch-odds-watchdog.timer`. When that stamp actually moves:

1. Emails **pingywon@gmail.com** (via the existing Brevo SMTP relay) that
   new data is available.
2. Triggers a full `scrape.py` run in the background (same non-blocking
   `subprocess.Popen` pattern as the "Refresh Data" button).
3. Records the new stamp in `watchdog_state.json` (gitignored, local state
   only) so it doesn't re-alert on the same update.

**Email only, by design** — SMS via AT&T's `txt.att.net` email-to-SMS gateway
was tried and confirmed dead (permanently shut down 2025-06-17; Brevo relayed
the message cleanly with no SMTP error, but nothing ever arrived — silent
drop is that gateway's documented post-shutdown behavior). Real SMS would
require registering the existing Telnyx number for A2P 10DLC messaging,
including handing over personal identity info (last 4 of SSN) through a
carrier verification flow — not pursued; the owner opted for email-only.

Install alongside the main service:
```bash
sudo cp deploy/pa-lottery-scratch-odds-watchdog.service deploy/pa-lottery-scratch-odds-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pa-lottery-scratch-odds-watchdog.timer
```

## Honest caveats

- Unofficial — not affiliated with or endorsed by the Pennsylvania Lottery.
- Data is scraped from public palottery.pa.gov and pacodeandbulletin.gov
  pages and only updates when someone clicks "Refresh Data" (no automatic
  background refresh — see "Refresh schedule" above).
- "Deal score" is this project's own weighted heuristic, not an official
  PA Lottery ranking.
- Ticket images are cached locally for the app to display but are **not**
  redistributed in this git repository (see `.gitignore`) — hundreds of
  official PA Lottery ticket-art JPEGs in public git history in perpetuity
  is a different exposure than displaying them locally.
- See the "data-honesty note" above regarding per-tier original counts.

## License

MIT — see `LICENSE`.
