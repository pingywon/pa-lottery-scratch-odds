#!/usr/bin/env python3
"""Watch for PA Lottery updating its own "wins remaining" data.

PA Lottery only updates their prizes-remaining figures irregularly (observed:
no change for 5+ days straight). Rather than re-scrape all ~140 pages on a
schedule, this just checks the single freshness stamp on prizes-remaining.aspx
every run (cheap - one request) and only triggers a full scrape.py refresh
plus an email alert when that stamp actually moves. Meant to run every 15
minutes via pa-lottery-scratch-odds-watchdog.timer.
"""
import json
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape  # reuse fetch(), parse_freshness_label(), REMAINING_URL, log()

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "watchdog_state.json"
BREVO_CREDS_FILE = Path.home() / ".brevo_smtp"

EMAIL_TO = "pingywon@gmail.com"
APP_URL = "http://192.168.13.131:8789/"


def log(msg):
    print(f"[watchdog] {msg}", file=sys.stderr, flush=True)


def load_brevo_creds():
    creds = {}
    for line in BREVO_CREDS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        creds[k.strip()] = v.strip()
    return creds


def send_email(to_addr, subject, body):
    creds = load_brevo_creds()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "PA Lottery Scratch Odds Watchdog <hello@gadgetconnections.com>"
    msg["To"] = to_addr
    with smtplib.SMTP(creds["SMTP_SERVER"], int(creds["SMTP_PORT"]), timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.login(creds["SMTP_LOGIN"], creds["SMTP_KEY"])
        server.sendmail(msg["From"], [to_addr], msg.as_string())


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def trigger_scrape():
    """Non-blocking, like server.py's /api/refresh - scrape.py's own
    scrape.lock guards against a concurrent run (button, timer, or this)."""
    subprocess.Popen([sys.executable, str(ROOT / "scrape.py")], cwd=str(ROOT))


def main():
    try:
        page_html = scrape.fetch(scrape.REMAINING_URL)
    except Exception as e:
        log(f"fetch failed (non-fatal, will retry next run): {e}")
        return

    current = scrape.parse_freshness_label(page_html)
    if current is None:
        log("could not parse freshness label from the page - site markup may have changed")
        return

    state = load_state()
    previous = state.get("last_seen_as_of")
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if previous is None:
        log(f"first run - recording baseline: {current!r}")
        save_state({"last_seen_as_of": current, "last_checked_at": checked_at})
        return

    if current == previous:
        log(f"no change ({current!r})")
        save_state({"last_seen_as_of": current, "last_checked_at": checked_at})
        return

    log(f"CHANGE DETECTED: {previous!r} -> {current!r}")
    save_state({
        "last_seen_as_of": current,
        "last_checked_at": checked_at,
        "last_change_detected_at": checked_at,
        "previous_as_of": previous,
    })

    email_body = (
        f"PA Lottery just updated its scratch-off Wins Remaining data.\n\n"
        f"Was: {previous}\n"
        f"Now: {current}\n\n"
        f"A fresh scrape of all active games is running now (takes a few "
        f"minutes). Check the app in a bit:\n{APP_URL}"
    )

    try:
        send_email(EMAIL_TO, "PA Lottery odds updated", email_body)
        log(f"email sent to {EMAIL_TO}")
    except Exception as e:
        log(f"email send failed: {e}")

    trigger_scrape()
    log("triggered a fresh scrape")


if __name__ == "__main__":
    main()
