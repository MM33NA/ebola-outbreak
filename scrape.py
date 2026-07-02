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

# WHO DON pages use a sequential numbering scheme. Rather than hardcoding a
# specific DON number (which goes stale as new reports are published), we
# try a small window of recent DON numbers in descending order and take the
# first one that fetches and parses successfully. This way the scraper stays
# current across new Thursday DON publications without any code changes.
# DON608 was current as of 19 June 2026 — start search 5 above to catch
# any new ones, fall back up to 5 below to handle gaps.
WHO_DON_BASE = "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON"
WHO_DON_SEARCH_START = 613  # try from here downward
WHO_DON_SEARCH_RANGE = 10   # how many to try before giving up

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
    Detect a Cloudflare challenge page. These return HTTP 200 but contain
    JS-challenge boilerplate. Three reliable signals, any one is sufficient:
      1. cf-mitigated: challenge header
      2. challenges.cloudflare.com in body
      3. 'just a moment' in body
    """
    cf_mitigated = response.headers.get("cf-mitigated", "").lower()
    if "challenge" in cf_mitigated:
        print(f"  Cloudflare block: cf-mitigated={cf_mitigated}")
        return True
    body = response.text.lower()
    if "challenges.cloudflare.com" in body:
        print("  Cloudflare block: challenges.cloudflare.com in body.")
        return True
    if "just a moment" in body:
        print("  Cloudflare block: 'just a moment' in body.")
        return True
    return False


def fetch_who_don():
    """
    Try WHO DON pages from WHO_DON_SEARCH_START downward, returning the
    first one that (a) fetches successfully and (b) contains parseable
    DRC case/death numbers. This auto-discovers the latest DON without
    hardcoding a specific number.
    """
    import requests
    for don_num in range(WHO_DON_SEARCH_START, WHO_DON_SEARCH_START - WHO_DON_SEARCH_RANGE, -1):
        url = f"{WHO_DON_BASE}{don_num}"
        try:
            resp = _get(url)
            if resp.status_code == 404:
                continue
            if is_cloudflare_block(resp):
                print(f"  WHO DON{don_num} Cloudflare-blocked, trying next...")
                continue
            # Quick check that this DON is about this outbreak and has parseable numbers
            clean = to_clean_text(resp.text)
            if parse_drc(clean) is not None:
                print(f"  WHO DON{don_num}: parseable outbreak data found.")
                return resp.text, f"WHO_DON{don_num}"
            else:
                print(f"  WHO DON{don_num}: fetched but no DRC data found "
                      f"(may be a different outbreak or format). Trying next...")
        except Exception as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            if status == 404:
                continue  # expected for DON numbers that don't exist yet
            print(f"  WHO DON{don_num} fetch error: {e}")
    return None, None


def fetch_page():
    """
    WHO is tried FIRST — no Cloudflare, consistent sentence patterns, no
    wording changes between updates. ECDC is fallback only.

    WHY WHO IS NOW PRIMARY:
      ECDC has two persistent problems from GitHub Actions runners:
      1. Cloudflare managed-challenge blocks (HTTP 200 but JS-challenge
         content), which our detection has missed twice because ECDC's
         challenge page is ~120KB — the same size as the real page.
      2. ECDC also changed their sentence wording on 1 July 2026:
         OLD: 'DRC Ministry of Health reported a total of X confirmed
              cases, including Y confirmed related deaths'
         NEW: 'National Institute of Public Health reported a total of
              X confirmed cases and Y total related deaths'
         This broke the regex regardless of Cloudflare status.
      WHO DON pages: no Cloudflare, consistent sentence patterns, and
      published every Thursday so they stay current within a week.
    """
    print("Trying WHO DON (primary source)...")
    html, source = fetch_who_don()
    if html is not None:
        print(f"  Using {source} as data source.")
        return html, source

    print("WHO DON unavailable. Trying ECDC (fallback)...")
    try:
        resp = _get(ECDC_URL)
        print(f"  ECDC responded — {len(resp.text):,} chars, status {resp.status_code}.")
        if is_cloudflare_block(resp):
            print("FATAL: ECDC is Cloudflare-blocked and WHO DON unavailable.")
            sys.exit(1)
        return resp.text, "ECDC"
    except Exception as e:
        print(f"FATAL: ECDC also failed: {e}")
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
    Matches DRC case/death sentences. Three confirmed real-world patterns:

    PATTERN A — ECDC and early WHO DONs (DON603-605):
      "the DRC Ministry of Health reported a total of 1 155 confirmed
       cases, including 304 confirmed related deaths"

    PATTERN B — WHO DON608 (June 19 2026):
      "a cumulative of 896 confirmed cases, including 232 deaths, have
       been reported from the Democratic Republic of the Congo"

    PATTERN C — New ECDC wording (seen July 2026 in search snippet):
      "National Institute of Public Health reported a total of 1 333
       confirmed cases and 399 total related deaths"

    Numbers may use space or comma as thousands separator.
    """
    # Pattern A: Ministry/Institute reported a total of X... including/and Y deaths
    m = re.search(
        r'(?:DRC Ministry of Health|National Institute of Public Health)\s+reported'
        r'\s+a\s+total\s+of\s+([\d ,]+?)\s+confirmed\s+cases'
        r'(?:\s*,\s*including|\s+and)\s+([\d ,]+?)\s+'
        r'(?:total\s+)?(?:confirmed\s+)?(?:related\s+)?deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern B: cumulative of X confirmed cases, including Y deaths ... DRC
    m = re.search(
        r'cumulative\s+of\s+([\d ,]+?)\s+confirmed\s+cases,?\s+including\s+([\d ,]+?)\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    return None


def parse_uganda(clean_text):
    """
    Matches Uganda case/death sentences. Two confirmed real-world patterns:

    ECDC:
      "Uganda had reported a total of 20 confirmed cases, including two deaths"

    WHO DON608:
      "Uganda has reported 19 confirmed cases including two deaths"

    Death count is often a written-out word for small numbers.
    """
    m = re.search(
        r'Uganda\s+(?:had\s+|has\s+)?reported(?:\s+a\s+total\s+of)?\s+([\d ,]+?)\s+confirmed\s+cases'
        r',?\s+including\s+(\w+)\s+deaths?',
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
