"""
scrape.py - Ebola outbreak data scraper (ECDC source)

WHY ECDC:
  ECDC's outbreak page updates almost daily with exact DRC and Uganda
  case/death counts in consistent, parseable sentences. CDC uses rounded
  prose; WHO DON pages only publish weekly. ECDC is the right source.

WHY cloudscraper:
  ECDC sits behind Cloudflare. GitHub Actions runners use Azure datacenter
  IPs that Cloudflare consistently blocks with a managed JS challenge —
  returning HTTP 200 with ~120KB of challenge HTML instead of real content.
  Plain requests can't solve the JS challenge. cloudscraper executes it.

Run: python scrape.py
Requires: pip install requests cloudscraper pytrends  (see update.yml)
"""

import re
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ECDC_URL = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"
JSON_FILE = Path(__file__).parent / "data.json"

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def fetch_page():
    """
    Fetch the ECDC outbreak page using cloudscraper, which handles
    Cloudflare managed challenges that plain requests cannot bypass.
    """
    try:
        import cloudscraper
    except ImportError:
        print("FATAL: cloudscraper is not installed.")
        print("Add it to update.yml: pip install requests cloudscraper pytrends")
        sys.exit(1)

    print(f"Fetching ECDC: {ECDC_URL} ...")
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        response = scraper.get(ECDC_URL, timeout=30)
        response.raise_for_status()
        print(f"  Responded — {len(response.text):,} characters, status {response.status_code}.")
        return response.text, "ECDC"
    except Exception as e:
        print(f"FATAL: ECDC fetch failed: {e}")
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
    """Parse a number that may use space/comma as thousands separator or be a word."""
    token = token.strip().lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    return int(token.replace(',', '').replace(' ', ''))


def parse_last_updated(clean_text):
    """
    ECDC states this near the top:
      "It was last updated 2 July at 15:00."
    Falls back to today's date if not found.
    """
    m = re.search(
        r'last updated\s+(\d{1,2}\s+\w+)(?:\s+\d{4})?\s+at\s+\d{1,2}:\d{2}',
        clean_text, re.IGNORECASE
    )
    if not m:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    day_month = m.group(1).strip()
    year_match = re.search(r'As of\s+\d{1,2}\s+\w+\s+(\d{4})', clean_text)
    year = year_match.group(1) if year_match else str(datetime.now(timezone.utc).year)

    try:
        dt = datetime.strptime(f"{day_month} {year}", "%d %B %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_drc(clean_text):
    """
    Matches DRC case/death sentences. Multiple confirmed real-world patterns:

    PATTERN A — ECDC wording (pre-July 2026):
      "DRC Ministry of Health reported a total of 1 155 confirmed
       cases, including 304 confirmed related deaths"

    PATTERN B — ECDC wording (July 2026):
      "Democratic Republic of the Congo (DRC) reported a total of
       1 406 confirmed cases ... A total 438 related deaths"

    PATTERN C — ECDC wording variant (seen in search snippet):
      "National Institute of Public Health reported a total of 1 333
       confirmed cases and 399 total related deaths"

    Numbers may use space or comma as thousands separator (e.g. "1 406" or "1,406").
    """
    # Pattern A/C: Ministry/Institute/DRC reported a total of X cases... Y deaths
    m = re.search(
        r'(?:DRC Ministry of Health|National Institute of Public Health|'
        r'Democratic Republic of the Congo\s*\([^)]*\))\s+reported\s+a\s+total\s+of'
        r'\s+([\d ,]+?)\s+confirmed\s+cases'
        r'(?:\s*[,(].*?(?:\)|,))?\s*'
        r'(?:,\s*including|\s+and|\.\s*A\s+total)\s+([\d ,]+?)\s+'
        r'(?:total\s+)?(?:confirmed\s+)?(?:related\s+)?deaths',
        clean_text, re.IGNORECASE | re.DOTALL
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern B fallback: simpler split approach for "X confirmed cases...Y...deaths"
    m = re.search(
        r'([\d ,]{3,})\s+confirmed\s+cases.*?'
        r'([\d ,]{3,})\s+(?:total\s+)?(?:confirmed\s+)?(?:related\s+)?deaths',
        clean_text[:2000], re.IGNORECASE | re.DOTALL
    )
    if m:
        cases = parse_int_token(m.group(1))
        deaths = parse_int_token(m.group(2))
        if cases > 0 and deaths > 0 and cases > deaths:
            return {"cases": cases, "deaths": deaths}

    return None


def parse_uganda(clean_text):
    """
    Matches Uganda case/death sentences:
      "Uganda had reported a total of 20 confirmed cases, including two deaths"
      "Uganda has reported 19 confirmed cases including two deaths"
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
    """Load data.json preserving events/trends; start clean on corruption."""
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
        print(f"⚠️ data.json unreadable ({e}). Starting clean.")
        return baseline

    if not isinstance(loaded, dict):
        return baseline

    for key in ("summary", "timeline", "events"):
        loaded.setdefault(key, baseline[key])
    loaded.setdefault("google_trends_surveillance", {})

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
    print("Ebola Scraper — ECDC via cloudscraper")
    print("========================================")

    html_content, source = fetch_page()
    clean_text = to_clean_text(html_content)

    updated_date = parse_last_updated(clean_text)
    drc = parse_drc(clean_text)
    uganda = parse_uganda(clean_text)

    if drc is None:
        print("FATAL: could not parse DRC case/death numbers from ECDC page.")
        print("ECDC may have changed their wording again — update parse_drc().")
        print("First 500 chars of clean text for diagnosis:")
        print(clean_text[:500])
        sys.exit(1)

    if uganda is None:
        print("FATAL: could not parse Uganda case/death numbers from ECDC page.")
        print("ECDC may have changed their wording — update parse_uganda().")
        sys.exit(1)

    confirmed_drc = drc["cases"]
    confirmed_deaths = drc["deaths"]
    uganda_cases = uganda["cases"]
    uganda_deaths = uganda["deaths"]
    total_cases = confirmed_drc + uganda_cases
    total_deaths = confirmed_deaths + uganda_deaths

    if total_cases == 0:
        print("FATAL: parsed all-zero case counts — treating as parse failure.")
        sys.exit(1)

    cfr_percent = round((total_deaths / total_cases) * 100, 1) if total_cases else 0.0

    print(f"Source: {source}")
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