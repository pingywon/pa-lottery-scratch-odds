# PA Lottery Scratch Odds

A sortable, searchable board of every active Pennsylvania Lottery scratch-off
game — price, odds, prize tiers, and how many top prizes are actually still
out there — using the real official ticket artwork.

**Unofficial. Not affiliated with, endorsed by, or connected to the
Pennsylvania Lottery in any way.** This is a personal data-transparency
project built entirely on public pages at palottery.pa.gov.

## What this is

For each active game, the app shows three distinct odds figures rather than
one ambiguous number:

1. **Odds of winning any prize (published)** — the official static figure
   PA Lottery prints on the game's page (e.g. "1 in 3.63").
2. **Adjusted top-prize odds (live)** — recalculated right now using the
   game's *exact* original ticket count (pulled from its official
   Pennsylvania Bulletin regulatory filing) and the *live* remaining count
   for the 6 prize tiers PA Lottery actually tracks in real time. This is an
   exact calculation, not an estimate — but it's honestly scoped to just
   those 6 tiers, since PA Lottery doesn't publish live remaining counts for
   every smaller/common prize.
3. **% of top prizes remaining** (e.g. "5 of 5", shown as a progress bar) —
   the simplest, most literal "are there still big prizes left" signal.

A **deal score** (0–100) blends (3) and (1) — weighted 60/40 toward "top
prizes still remaining" — into a single one-click "Best Deals" sort.

Every card uses the game's actual scratch-ticket artwork (front and back),
scraped directly from PA Lottery's own image hosting.

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
sudo cp deploy/pa-lottery-scratch-odds.service deploy/pa-lottery-scratch-odds-scrape.service deploy/pa-lottery-scratch-odds-scrape.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pa-lottery-scratch-odds.service
sudo systemctl enable --now pa-lottery-scratch-odds-scrape.timer
```

Run `scrape.py` manually once before first starting the web service, so
`data.json` exists — the server tolerates a missing `data.json` (shows an
empty board) but there's no reason to start it empty.

## Refresh schedule

The scrape timer runs once daily (06:30 + up to 10 min random jitter) since
PA Lottery's own "wins remaining" data itself is only updated roughly daily.
The server re-reads `data.json` from disk on every request, so a fresh
scrape shows up on the very next page load — no restart needed.

## Honest caveats

- Unofficial — not affiliated with or endorsed by the Pennsylvania Lottery.
- Data is scraped from public palottery.pa.gov and pacodeandbulletin.gov
  pages and may lag the live site by up to a day.
- "Deal score" is this project's own weighted heuristic, not an official
  PA Lottery ranking.
- Ticket images are cached locally for the app to display but are **not**
  redistributed in this git repository (see `.gitignore`) — hundreds of
  official PA Lottery ticket-art JPEGs in public git history in perpetuity
  is a different exposure than displaying them locally.
- See the "data-honesty note" above regarding per-tier original counts.

## License

MIT — see `LICENSE`.
