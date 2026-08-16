#!/usr/bin/env python3
"""Scrape palottery.pa.gov for active PA Lottery scratch-off game data.

Crawls the active-games roster, each game's detail page, and each game's
official Pennsylvania Bulletin regulatory filing, then writes data.json and
caches ticket art locally. See README.md for the field-by-field data model
and why some odds figures are exact while others are intentionally omitted
rather than estimated.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.palottery.pa.gov"
ACTIVE_URL = f"{BASE}/Scratch-Offs/Active-Games.aspx"
REMAINING_URL = f"{BASE}/scratch-offs/prizes-remaining.aspx"
DETAIL_URL_FMT = f"{BASE}/Scratch-Offs/View-Scratch-Off.aspx?id={{}}"
USER_AGENT = "PALotteryScratchOddsBot/1.0 (personal informational project; contact pingywon@gmail.com)"
RATE_LIMIT_SECONDS = 0.75
MIN_SUCCESS_RATIO = 0.5
MIN_IMAGE_BYTES = 5 * 1024

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
IMAGES_DIR = ROOT / "images"
LOCK_FILE = ROOT / "scrape.lock"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else


def acquire_lock():
    """Cooperative lock so a manually-triggered refresh (server.py) and the
    daily systemd timer can't run concurrently and double-hit palottery.pa.gov.
    Guards regardless of which of the two spawned this process."""
    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid and pid_alive(existing_pid):
            log(f"Another scrape is already running (pid {existing_pid}) — exiting")
            sys.exit(2)
        log("Found stale lock file from a dead process — reclaiming it")
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass

BADGE_ALTS = {
    "second_chance": "Second Chance Eligible",
    "online_game": "Available as Online Game",
    "fast_play": "Available as Fast Play",
    "sales_ending_soon": "Sales Ending Soon",
}


def log(msg):
    print(f"[scrape] {msg}", file=sys.stderr, flush=True)


def fetch(url, binary=False, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                return data if binary else data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5)
    raise last_err


def to_int(s):
    return int(s.strip().replace(",", "").lstrip("$"))


# ---------------------------------------------------------------------------
# Active-Games.aspx: full roster in one request
# ---------------------------------------------------------------------------

TILE_RE = re.compile(
    r'<a href="/Scratch-Offs/View-Scratch-Off\.aspx\?id=(\d+)" class="activeGame_li[^>]*>(.*?)</a>',
    re.S,
)


def parse_active_games(page_html):
    games = []
    for m in TILE_RE.finditer(page_html):
        gid, block = int(m.group(1)), m.group(2)
        name_m = re.search(r"<p>(.*?)</p>", block, re.S)
        name = html.unescape(name_m.group(1).strip()) if name_m else None
        price_m = re.search(r'data-search="\$?([\d,]+)"', block)
        price = to_int(price_m.group(1)) if price_m else None
        badges = {key: (f'alt="{label}"' in block) for key, label in BADGE_ALTS.items()}
        badges["new"] = "new-tag" in block
        games.append({"id": gid, "name": name, "price": price, "badges": badges})
    return games


# ---------------------------------------------------------------------------
# View-Scratch-Off.aspx?id=NNNN: per-game detail page
# ---------------------------------------------------------------------------

def parse_detail_page(page_html):
    out = {}

    m = re.search(r"<h2[^>]*>\s*(.*?)\s*\(PA&#8209;(\d+)\)", page_html, re.S)
    if m:
        out["name"] = html.unescape(m.group(1).strip())
        out["game_number"] = f"PA-{m.group(2)}"

    m = re.search(
        r"is a \$([\d,]+) game that offers ([\d,]+) Top Prizes? of \$([\d,]+)",
        page_html,
    )
    if m:
        out["price"] = to_int(m.group(1))
        out["top_prize_original"] = to_int(m.group(2))
        out["top_prize_amount"] = to_int(m.group(3))

    m = re.search(r"Overall chances of winning a prize:\s*1:([\d.]+)", page_html)
    if m:
        out["overall_odds_published"] = float(m.group(1))

    tiers_live = []
    m = re.search(r'<table class="table-global">(.*?)</table>', page_html, re.S)
    if m:
        for row in re.finditer(
            r"<tr[^>]*>\s*<td>\s*\$([\d,]+)\s*</td>\s*<td>\s*([\d,]+)\s*</td>\s*</tr>",
            m.group(1),
        ):
            tiers_live.append({"amount": to_int(row.group(1)), "remaining": to_int(row.group(2))})
    out["tiers_live"] = tiers_live

    cov_m = re.search(r"var coveredImage = '([^']+)'", page_html) or re.search(
        r'<img src="([^"]+)" alt="Covered View"', page_html
    )
    unc_m = re.search(r"var uncoveredImage = '([^']+)'", page_html) or re.search(
        r'<img src="([^"]+)" alt="Uncovered View"', page_html
    )
    out["image_cover_url"] = cov_m.group(1) if cov_m else None
    out["image_uncovered_url"] = unc_m.group(1) if unc_m else None

    m = re.search(
        r'href="(https://www\.pacodeandbulletin\.gov/[^"]+)"[^>]*>\s*Complete Game Rules',
        page_html,
    )
    out["bulletin_url"] = m.group(1) if m else None

    return out


# ---------------------------------------------------------------------------
# Pennsylvania Bulletin regulatory filing: full official prize-tier table
# ---------------------------------------------------------------------------

def parse_bulletin_page(page_html):
    """Return (total_tickets_printed, {amount: original_winner_count})."""
    total_tickets = None
    m = re.search(r"Approximately\s+([\d,]+)\s+tickets will be printed", page_html)
    if m:
        total_tickets = to_int(m.group(1))
    if total_tickets is None:
        m = re.search(r"Per\s*([\d,]+)\s*Tickets", page_html)
        if m:
            total_tickets = to_int(m.group(1))

    tier_originals = defaultdict(int)
    table_m = re.search(r'<table class="miscr">(.*?)</table>', page_html, re.S)
    if table_m:
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_m.group(1), re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
            if len(tds) < 3:
                continue
            def clean(x):
                return html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
            win_raw, winners_raw = clean(tds[-3]), clean(tds[-1]).replace(",", "")
            win_m = re.match(r"\$([\d,]+)", win_raw)
            if not win_m or not winners_raw.isdigit():
                continue
            tier_originals[to_int(win_m.group(1))] += int(winners_raw)

    return total_tickets, dict(tier_originals)


# ---------------------------------------------------------------------------
# prizes-remaining.aspx: just the global "as of" freshness label
# ---------------------------------------------------------------------------

def parse_freshness_label(page_html):
    m = re.search(r"Wins Remaining were updated on ([^.<]+)\.", page_html)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Image caching
# ---------------------------------------------------------------------------

def cache_image(url, dest_dir, dest_name):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / dest_name
    rel_path = f"images/{dest_dir.name}/{dest_name}"
    if dest_path.exists() and dest_path.stat().st_size >= MIN_IMAGE_BYTES:
        return rel_path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log(f"  image fetch failed ({url}): {e}")
        return None
    if not ctype.startswith("image/") or len(data) < MIN_IMAGE_BYTES:
        log(f"  image failed validation ({url}): type={ctype} size={len(data)}")
        return None
    dest_path.write_bytes(data)
    return rel_path


# ---------------------------------------------------------------------------
# Derived fields + assembly
# ---------------------------------------------------------------------------

def build_game_record(roster_entry, detail, bulletin_total_tickets, bulletin_tiers, images):
    price = detail.get("price", roster_entry.get("price"))
    tiers_live = detail.get("tiers_live") or []

    # Some games ship with an empty CMS description and never get the "offers
    # N Top Prizes of $X" sentence at all; "X per year/month for life" games
    # have a sentence but it names the per-period figure (e.g. $1,000,000),
    # not the annuitized lump-sum total the live tracked-tiers table uses
    # (e.g. $14,500,000) - so the two amounts never match. Either way, fall
    # back to the highest tracked tier (the site's own "Top Six Prizes" table
    # is already ranked highest first) and the Bulletin's exact original
    # count for that amount.
    live_amounts = {t["amount"] for t in tiers_live}
    top_prize_amount = detail.get("top_prize_amount")
    if (top_prize_amount is None or top_prize_amount not in live_amounts) and tiers_live:
        top_prize_amount = max(live_amounts)
    top_prize_original = detail.get("top_prize_original")
    if top_prize_original is None and top_prize_amount is not None:
        top_prize_original = (bulletin_tiers or {}).get(top_prize_amount)

    top_prize_remaining = next(
        (t["remaining"] for t in tiers_live if t["amount"] == top_prize_amount), None
    )

    pct_top_prizes_remaining = None
    if top_prize_original and top_prize_remaining is not None and top_prize_original > 0:
        pct_top_prizes_remaining = round(100 * top_prize_remaining / top_prize_original, 1)

    tiers = []
    live_by_amount = {t["amount"]: t["remaining"] for t in tiers_live}
    all_amounts = sorted(set(live_by_amount) | set(bulletin_tiers or {}), reverse=True)
    for amount in all_amounts:
        remaining = live_by_amount.get(amount)
        original = (bulletin_tiers or {}).get(amount)
        published_odds = None
        if bulletin_total_tickets and original:
            published_odds = round(bulletin_total_tickets / original, 2)
        adjusted_odds_now = None
        if bulletin_total_tickets and remaining:
            adjusted_odds_now = round(bulletin_total_tickets / remaining, 2)
        tiers.append({
            "amount": amount,
            "original": original,
            "original_understated": bool(original and remaining and remaining > original),
            "published_odds": published_odds,
            "remaining": remaining,
            "adjusted_odds_now": adjusted_odds_now,
            "tracked_live": amount in live_by_amount,
        })

    top6_adjusted_odds_now = None
    if bulletin_total_tickets and tiers_live and len(tiers_live) > 0:
        remaining_sum = sum(t["remaining"] for t in tiers_live)
        if remaining_sum > 0:
            top6_adjusted_odds_now = round(bulletin_total_tickets / remaining_sum, 2)

    # Value score: expected payout per ticket, right now, as a % of price.
    # For each tier, use today's live remaining count where PA Lottery tracks
    # one (the top 6); for smaller/common tiers with no live number, assume
    # the original day-one count still holds (the best available estimate,
    # documented as an assumption - it can only overstate value slightly,
    # since untracked small prizes do get claimed over time too, we just
    # can't see how much). 100% would mean "expected to pay back every
    # dollar wagered, on average" - real games run well under that by design.
    value_score_pct = None
    if bulletin_total_tickets and price:
        payout_sum = 0
        have_tier_data = False
        for t in tiers:
            count = t["remaining"] if t["tracked_live"] else t["original"]
            if count is not None:
                payout_sum += count * t["amount"]
                have_tier_data = True
        if have_tier_data:
            value_score_pct = round(100 * (payout_sum / bulletin_total_tickets) / price, 2)

    overall_odds_published = detail.get("overall_odds_published")
    badges = dict(roster_entry.get("badges") or {})

    return {
        "id": roster_entry["id"],
        "game_number": detail.get("game_number"),
        "name": detail.get("name") or roster_entry.get("name"),
        "price": price,
        "overall_odds_published": overall_odds_published,
        "total_tickets_printed": bulletin_total_tickets,
        "top_prize_amount": top_prize_amount,
        "top_prize_original": top_prize_original,
        "top_prize_remaining": top_prize_remaining,
        "pct_top_prizes_remaining": pct_top_prizes_remaining,
        "top6_adjusted_odds_now": top6_adjusted_odds_now,
        "value_score_pct": value_score_pct,
        "tiers": tiers,
        "badges": badges,
        "images": {
            "cover": images.get("cover"),
            "uncovered": images.get("uncovered"),
        },
        "detail_url": DETAIL_URL_FMT.format(roster_entry["id"]),
        "bulletin_url": detail.get("bulletin_url"),
    }


# ---------------------------------------------------------------------------
# Main crawl
# ---------------------------------------------------------------------------

def load_previous():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def main():
    acquire_lock()
    try:
        _run()
    finally:
        release_lock()


def _run():
    log(f"Fetching roster: {ACTIVE_URL}")
    roster = parse_active_games(fetch(ACTIVE_URL))
    log(f"Found {len(roster)} active games")

    try:
        freshness = parse_freshness_label(fetch(REMAINING_URL))
    except Exception as e:
        log(f"prizes-remaining.aspx fetch failed (non-fatal): {e}")
        freshness = None
    time.sleep(RATE_LIMIT_SECONDS)

    previous = load_previous()
    previous_by_id = {g["id"]: g for g in (previous or {}).get("games", [])} if previous else {}

    games = []
    failures = 0
    for entry in roster:
        gid = entry["id"]
        try:
            detail_html = fetch(DETAIL_URL_FMT.format(gid))
            detail = parse_detail_page(detail_html)
            time.sleep(RATE_LIMIT_SECONDS)

            bulletin_total_tickets, bulletin_tiers = None, {}
            if detail.get("bulletin_url"):
                try:
                    bulletin_html = fetch(detail["bulletin_url"])
                    bulletin_total_tickets, bulletin_tiers = parse_bulletin_page(bulletin_html)
                except Exception as e:
                    log(f"  [{gid}] bulletin fetch/parse failed (degraded, non-fatal): {e}")
                time.sleep(RATE_LIMIT_SECONDS)

            images = {}
            game_number = detail.get("game_number", f"id{gid}")
            if detail.get("image_cover_url"):
                fname = detail["image_cover_url"].rsplit("/", 1)[-1]
                images["cover"] = cache_image(detail["image_cover_url"], IMAGES_DIR / "covers", fname)
            if detail.get("image_uncovered_url"):
                fname = detail["image_uncovered_url"].rsplit("/", 1)[-1]
                images["uncovered"] = cache_image(detail["image_uncovered_url"], IMAGES_DIR / "backs", fname)

            record = build_game_record(entry, detail, bulletin_total_tickets, bulletin_tiers, images)
            games.append(record)
            log(f"  [{gid}] OK: {record['game_number']} {record['name']!r}")
        except Exception as e:
            failures += 1
            log(f"  [{gid}] FAILED: {e}")
            if gid in previous_by_id:
                stale = dict(previous_by_id[gid])
                games.append(stale)
                log(f"  [{gid}] kept previous cached record")

    success_count = len(roster) - failures
    if previous and success_count < MIN_SUCCESS_RATIO * previous.get("game_count", 0):
        log(
            f"REFUSING TO WRITE data.json: only {success_count} games succeeded "
            f"vs previous {previous.get('game_count')} (< {MIN_SUCCESS_RATIO:.0%} threshold)"
        )
        sys.exit(1)

    output = {
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prizes_remaining_as_of": freshness,
        "game_count": len(games),
        "games": games,
    }
    DATA_FILE.write_text(json.dumps(output, indent=2))
    log(f"Wrote {DATA_FILE} with {len(games)} games ({failures} failures)")


if __name__ == "__main__":
    main()
