"""
scrape.py - Ebola outbreak data scraper (ECDC source)

WHY ECDC AND NOT CDC:
  CDC's "Ebola Disease: Current Situation" page stopped publishing exact
  case/death counts in a stable format and now uses rounded prose
  ("more than 1,000 cases"). That can't be parsed reliably into a daily
  timeline. ECDC's outbreak page is updated almost daily and consistently
  states DRC and Uganda totals as exact numbers in predictable sentences:

    "On 25 June, the DRC Ministry of Health reported a total of 1 155
     confirmed cases, including 304 confirmed related deaths, and 385
     individuals hospitalised in isolation (as of 24 June)."

    "As of 25 June, Uganda had reported a total of 20 confirmed cases,
     including two deaths."

  This script parses those two sentence patterns. If either pattern is
  not found, the script HARD FAILS (sys.exit(1)) rather than writing
  placeholder/fallback numbers. A previous version of this script used
  hardcoded fallback values that silently never updated for weeks because
  the page structure it expected (a "DRC (As of...) / Uganda (As of...)"
  table) never matched anything CDC actually publishes. That mistake is
  not repeated here: there is no fallback dict. Either we parse real
  numbers, or we fail loudly so the GitHub Action shows a red X.

OUTPUT STRUCTURE (what index.html actually reads):
  data.json:
    {
      "updated": "YYYY-MM-DD",
      "summary": {
        "confirmedDRC": int,      DRC confirmed cases
        "confirmedDeaths": int,   DRC confirmed deaths
        "ugandaCases": int,       Uganda confirmed cases
        "ugandaDeaths": int,      Uganda confirmed deaths
        "cfrPercent": float       (confirmedDeaths+ugandaDeaths)/(confirmedDRC+ugandaCases)*100
      },
      "timeline": [ {"date": "YYYY-MM-DD", "cases": int, "deaths": int}, ... ],
      "events": [ ... ],                      # NOT touched by this script - hand-maintained
      "google_trends_surveillance": { ... }    # NOT touched by this script - owned by trends.py
    }

OWNERSHIP MODEL (unchanged from project convention):
  - This script owns: summary.*, timeline[] (appends/updates only, by date)
  - This script never touches: events[], google_trends_surveillance
  - Manually maintained: events[]

Run: python scrape.py
"""

import re
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ECDC_URL = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"

# WHO DON608 is the most recent Disease Outbreak News with exact confirmed
# case/death counts in parseable sentence form. The WHO emergencies/alert-and-response
# page updates daily but uses different sentence patterns — DON pages are
# more consistent for parsing. WHO.int does not use Cloudflare.
WHO_DON_URL = "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON608"

JSON_FILE = Path(__file__).parent / "data.json"

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _get(url):
    """Fetch a URL with full browser-like headers."""
    import requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response


def is_cloudflare_block(response):
    """
    Detect a Cloudflare challenge/block page reliably.

    IMPORTANT: the previous version used a character-count check
    (< 20,000 chars) which failed because ECDC's Cloudflare managed
    challenge page is ~120KB — the same size as the real page — so the
    size check never fired. The correct signals are:

      1. 'cf-mitigated: challenge' response HEADER — the single most
         reliable signal, set by Cloudflare on managed challenges even
         when they return HTTP 200. Check headers first.
      2. 'challenges.cloudflare.com' in the page BODY — present in the
         JS challenge script src that Cloudflare injects.
      3. 'just a moment' in the page title/body — Cloudflare's loading
         message on the interstitial page.

    Any one of these is sufficient to declare a block.
    """
    # Signal 1: response header (most reliable, doesn't require body parsing)
    cf_mitigated = response.headers.get("cf-mitigated", "").lower()
    if "challenge" in cf_mitigated:
        print(f"  Cloudflare block detected via cf-mitigated header: {cf_mitigated}")
        return True

    body = response.text.lower()

    # Signal 2: Cloudflare's challenge script domain in the body
    if "challenges.cloudflare.com" in body:
        print("  Cloudflare block detected via challenges.cloudflare.com in body.")
        return True

    # Signal 3: Cloudflare's interstitial loading message
    if "just a moment" in body:
        print("  Cloudflare block detected via 'just a moment' in body.")
        return True

    return False


def fetch_page():
    """
    Fetch ECDC first. On Cloudflare block, fall back to WHO DON608.
    Returns (html_text, source_label).
    """
    import requests

    # --- Try ECDC ---
    print(f"Fetching ECDC: {ECDC_URL} ...")
    try:
        resp = _get(ECDC_URL)
        print(f"  ECDC responded — {len(resp.text):,} characters, "
              f"status {resp.status_code}.")
        if is_cloudflare_block(resp):
            print("  ECDC is Cloudflare-blocked. Falling back to WHO DON...")
        else:
            return resp.text, "ECDC"
    except Exception as e:
        print(f"  ECDC fetch failed: {e}. Falling back to WHO DON...")

    # --- Fall back to WHO DON608 ---
    print(f"Fetching WHO DON: {WHO_DON_URL} ...")
    try:
        resp = _get(WHO_DON_URL)
        print(f"  WHO DON responded — {len(resp.text):,} characters, "
              f"status {resp.status_code}.")
        if is_cloudflare_block(resp):
            print("FATAL: WHO DON also appears blocked. No sources available.")
            sys.exit(1)
        return resp.text, "WHO_DON"
    except Exception as e:
        print(f"FATAL: WHO DON fetch also failed: {e}")
        sys.exit(1)


def to_clean_text(html_content):
    """Strip tags/scripts down to plain text, collapse whitespace."""
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html_content,
                         flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r'<[^>]+>', ' ', no_scripts)
    # Normalize the non-breaking space ECDC uses inside numbers like "1 155"
    no_tags = no_tags.replace('\xa0', ' ').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', no_tags).strip()


def parse_int_token(token):
    """Parse a number that may be written with a space as thousands separator
    (ECDC style, e.g. '1 155') or as a word ('two')."""
    token = token.strip().lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    return int(token.replace(',', '').replace(' ', ''))


def parse_last_updated(clean_text):
    """
    ECDC states this near the top:
      "This page is updated as more information becomes available.
       It was last updated 26 June at 15:00."
    Falls back to today's date (UTC) if the sentence can't be found,
    since this field is informational, not load-bearing for hard-fail logic.
    """
    m = re.search(
        r'last updated\s+(\d{1,2}\s+\w+)(?:\s+\d{4})?\s+at\s+\d{1,2}:\d{2}',
        clean_text, re.IGNORECASE
    )
    if not m:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    day_month = m.group(1).strip()
    # ECDC doesn't repeat the year in "last updated" — infer it from
    # the explicit "As of <D> <Month> <YYYY>" sentence that follows shortly after.
    year_match = re.search(r'As of\s+\d{1,2}\s+\w+\s+(\d{4})', clean_text)
    year = year_match.group(1) if year_match else str(datetime.now(timezone.utc).year)

    try:
        dt = datetime.strptime(f"{day_month} {year}", "%d %B %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_drc(clean_text):
    """
    Matches sentences like:
      "the DRC Ministry of Health reported a total of 1 155 confirmed
       cases, including 304 confirmed related deaths"
    Numbers may contain a space as a thousands separator, hence [\\d ]+.
    """
    m = re.search(
        r'DRC Ministry of Health reported a total of\s+([\d ]+?)\s+confirmed cases,'
        r'\s+including\s+([\d ]+?)\s+confirmed\s+(?:related\s+)?deaths',
        clean_text, re.IGNORECASE
    )
    if not m:
        return None
    return {
        "cases": parse_int_token(m.group(1)),
        "deaths": parse_int_token(m.group(2)),
    }


def parse_uganda(clean_text):
    """
    Matches sentences like:
      "Uganda had reported a total of 20 confirmed cases, including
       two deaths"
    Death count is often spelled out as a word for small numbers.
    """
    m = re.search(
        r'Uganda had reported a total of\s+([\d ]+?)\s+confirmed cases,'
        r'\s+including\s+(\w+)\s+deaths?',
        clean_text, re.IGNORECASE
    )
    if not m:
        return None
    return {
        "cases": parse_int_token(m.group(1)),
        "deaths": parse_int_token(m.group(2)),
    }


def load_existing_data():
    """Load data.json if present and structurally sane; otherwise start clean.
    Never fabricates summary/timeline numbers — only preserves sections this
    script doesn't own (events, google_trends_surveillance)."""
    baseline = {
        "updated": None,
        "summary": {},
        "timeline": [],
        "events": [],
        "google_trends_surveillance": {},
    }
    if not JSON_FILE.exists():
        return baseline

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception as e:
        print(f"⚠️ Existing data.json unreadable ({e}). Starting from a clean structure.")
        return baseline

    if not isinstance(loaded, dict):
        print("⚠️ Existing data.json is not an object. Starting from a clean structure.")
        return baseline

    # Migrate legacy flat structure (top-level confirmed/confirmed_deaths/etc.)
    # into the nested summary/timeline structure index.html expects, but only
    # as historical carry-over — this run's fresh scrape still overwrites summary.
    for key in ("summary", "timeline", "events"):
        loaded.setdefault(key, baseline[key])
    loaded.setdefault("google_trends_surveillance", {})

    # Drop legacy flat fields from the old (pre-summary/timeline) schema.
    # These are dead weight now that index.html only reads data.summary /
    # data.timeline. Safe to discard because this run regenerates summary
    # fresh from ECDC anyway.
    for legacy_key in ("suspected", "confirmed", "suspected_deaths",
                       "confirmed_deaths", "uganda_cases", "uganda_deaths"):
        loaded.pop(legacy_key, None)

    return loaded


def update_timeline(timeline, date_str, cases, deaths):
    existing = next((item for item in timeline if item.get("date") == date_str), None)
    if existing:
        if existing.get("cases") == cases and existing.get("deaths") == deaths:
            print(f"Timeline entry for {date_str} unchanged.")
        else:
            existing["cases"] = cases
            existing["deaths"] = deaths
            print(f"Updated existing timeline entry for {date_str}.")
    else:
        timeline.append({"date": date_str, "cases": cases, "deaths": deaths})
        print(f"Added new timeline entry for {date_str}.")
    timeline.sort(key=lambda x: x["date"])
    return timeline


def scrape_ebola_data():
    print("========================================")
    print("Ebola Scraper — ECDC + WHO DON fallback")
    print("========================================")

    html_content, source = fetch_page()
    clean_text = to_clean_text(html_content)

    updated_date = parse_last_updated(clean_text)
    drc = parse_drc(clean_text)
    uganda = parse_uganda(clean_text)

    if drc is None:
        print(f"FATAL: could not find the DRC case/death sentence on the {source} page.")
        print("Both sources tried. Either both are blocked or both changed their wording.")
        print("Check the raw HTML by adding a debug print in fetch_page() to diagnose.")
        sys.exit(1)

    if uganda is None:
        print(f"FATAL: could not find the Uganda case/death sentence on the {source} page.")
        print("Both sources tried. Either both are blocked or both changed their wording.")
        sys.exit(1)

    confirmed_drc = drc["cases"]
    confirmed_deaths = drc["deaths"]
    uganda_cases = uganda["cases"]
    uganda_deaths = uganda["deaths"]

    total_cases = confirmed_drc + uganda_cases
    total_deaths = confirmed_deaths + uganda_deaths

    if total_cases == 0:
        print("FATAL: parsed all-zero case counts. Treating as a parse failure, not real data.")
        sys.exit(1)

    cfr_percent = round((total_deaths / total_cases) * 100, 1) if total_cases else 0.0

    print(f"Source used: {source}")
    print(f"Parsed — DRC: {confirmed_drc} cases / {confirmed_deaths} deaths | "
          f"Uganda: {uganda_cases} cases / {uganda_deaths} deaths | "
          f"CFR: {cfr_percent}% | as of {updated_date}")

    dashboard_data = load_existing_data()

    dashboard_data["updated"] = updated_date
    dashboard_data["summary"]["confirmedDRC"] = confirmed_drc
    dashboard_data["summary"]["confirmedDeaths"] = confirmed_deaths
    dashboard_data["summary"]["ugandaCases"] = uganda_cases
    dashboard_data["summary"]["ugandaDeaths"] = uganda_deaths
    dashboard_data["summary"]["cfrPercent"] = cfr_percent
    dashboard_data["summary"]["dataSource"] = source

    dashboard_data["timeline"] = update_timeline(
        dashboard_data["timeline"], updated_date, total_cases, total_deaths
    )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, indent=2)

    print(f"Success: wrote summary + timeline to {JSON_FILE}")
    return dashboard_data


if __name__ == "__main__":
    scrape_ebola_data()