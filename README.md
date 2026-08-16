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

Expanding a row shows the complete official prize-tier table (every tier,
not just the top 6), the game's actual ticket artwork front/back, and links
to the official PA Lottery page and Bulletin filing. A `deal_score` field is
still computed in `data.json` (60/40 blend of % top prizes remaining and
published odds) for anyone who wants it, but it isn't surfaced in the UI.

Data only updates when someone clicks **Refresh Data** in the app — see
"Refresh schedule" below.

## Quick start

```bash
python3 scrape.py   # populates data.json and images/ (takes a few minutes; ~140 requests)
python3 server.py   # serves the app
```

Then open `http://<this-machine>:8789/`.

## Config (env vars)

| Var    | Default   | Purpose                          |
|--------|-----------|-----------------------------------|
| `PORT` | `8789`    | HTTP port for `server.py`         |
| `BIND` | `0.0.0.0` | Bind address for `server.py`      |

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

**On-demand only** — there is no automatic background refresh. Data updates
only when someone clicks "Refresh Data" in the app (or runs `python3
scrape.py` / `POST /api/refresh` directly). This was a deliberate choice:
PA Lottery's own "wins remaining" figures are themselves only updated
irregularly (the app shows their own "as of" date so you can tell), so a
scheduled daily scrape was mostly re-fetching identical data — an on-demand
button avoids the unnecessary traffic to palottery.pa.gov and puts the
owner in control of when a refresh happens. A `scrape.lock` PID file
(managed by `scrape.py`) still guards against two refreshes running at once,
in case the daily timer is ever re-enabled alongside manual refreshes. The
server re-reads `data.json` from disk on every request, so a fresh scrape
shows up on the very next page load — no restart needed.

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
