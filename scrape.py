"""
scrape.py - Ebola outbreak data scraper (ECDC source)

WHY ECDC:
  ECDC's outbreak page updates almost daily with exact DRC and Uganda
  case/death counts in consistent, parseable sentences. CDC uses rounded
  prose; WHO DON pages only publish weekly. ECDC is the right source.

WHY cloudscraper:
  ECDC sits behind Cloudflare. GitHub Actions runners use Azure datacenter
  IPs that Cloudflare consistently blocks with a managed JS challenge.
  Plain requests can't solve it. cloudscraper executes the challenge JS.

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
    Fetch the ECDC outbreak page via ScraperAPI, which routes requests
    through residential IPs that Cloudflare does not block.

    WHY ScraperAPI:
      ECDC sits behind Cloudflare which permanently blocks GitHub Actions'
      Azure datacenter IP ranges at the network level. No Python library
      (requests, cloudscraper, selenium) can fix an IP-level block —
      the only solution is routing through a non-datacenter IP.
      ScraperAPI's free tier (1,000 requests/month) is sufficient for
      one daily scrape. The API key is stored in GitHub Secrets as
      SCRAPER_API_KEY and passed in as an environment variable.

    SETUP (one-time):
      1. Sign up at https://www.scraperapi.com (free tier)
      2. Copy your API key from the dashboard
      3. In your GitHub repo: Settings → Secrets → Actions →
         New repository secret → Name: SCRAPER_API_KEY, Value: your key
      4. update.yml already passes it as an env var (see that file)
    """
    import requests
    import os

    api_key = os.environ.get("SCRAPER_API_KEY", "")
    if not api_key:
        print("FATAL: SCRAPER_API_KEY environment variable is not set.")
        print("Add it to GitHub Secrets and update.yml (see scrape.py docstring).")
        sys.exit(1)

    proxy_url = f"https://api.scraperapi.com?api_key={api_key}&url={ECDC_URL}"
    print(f"Fetching ECDC via ScraperAPI ...")
    try:
        response = requests.get(proxy_url, timeout=60)
        response.raise_for_status()
        print(f"  Responded — {len(response.text):,} characters, status {response.status_code}.")
        return response.text
    except Exception as e:
        print(f"FATAL: ScraperAPI fetch failed: {e}")
        sys.exit(1)


def to_clean_text(html_content):
    """Strip tags/scripts, normalize whitespace."""
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html_content,
                        flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r'<[^>]+>', ' ', no_scripts)
    no_tags = no_tags.replace('\xa0', ' ').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', no_tags).strip()


def parse_int_token(token):
    token = token.strip().lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    return int(token.replace(',', '').replace(' ', ''))


def parse_last_updated(clean_text):
    """Extract 'last updated DD Month' from ECDC page header."""
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
    Match DRC case/death count from ECDC's sentence.

    Three known ECDC wordings, tried in order:

    A) "DRC Ministry of Health reported a total of 1 155 confirmed cases,
        including 304 confirmed related deaths"

    B) "DRC reported a total of 1 406 confirmed cases ... A total 438
        related deaths"  (July 2026 wording with split sentence)

    C) "National Institute of Public Health reported a total of 1 333
        confirmed cases and 399 total related deaths"
    """
    # Pattern A — inline: "X cases, including Y deaths"
    m = re.search(
        r'(?:DRC Ministry of Health|DRC)\s+(?:Ministry of Health\s+)?reported'
        r'\s+a\s+total\s+of\s+([\d ,]+?)\s+confirmed\s+cases'
        r'\s*,\s*including\s+([\d ,]+?)\s+confirmed\s+related\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern B — split sentence: "X confirmed cases ... A total Y related deaths"
    m = re.search(
        r'reported\s+a\s+total\s+of\s+([\d ,]+?)\s+confirmed\s+cases'
        r'[^.]*?\.\s*A\s+total\s+([\d ,]+?)\s+related\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern C — "and Y total related deaths"
    m = re.search(
        r'(?:National Institute of Public Health)\s+reported\s+a\s+total\s+of'
        r'\s+([\d ,]+?)\s+confirmed\s+cases\s+and\s+([\d ,]+?)\s+total\s+related\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    return None


def parse_uganda(clean_text):
    """
    Match Uganda case/death count from ECDC's sentence.
    Handles 'had reported' and 'has reported', with or without 'a total of'.
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
    baseline = {
        "updated": None, "summary": {}, "timeline": [],
        "events": [], "google_trends_surveillance": {},
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

    html_content = fetch_page()
    clean_text = to_clean_text(html_content)

    updated_date = parse_last_updated(clean_text)
    drc = parse_drc(clean_text)
    uganda = parse_uganda(clean_text)

    if drc is None:
        print("FATAL: could not parse DRC case/death numbers from ECDC page.")
        print("ECDC may have changed their wording — check parse_drc().")
        print("First 800 chars of clean text for diagnosis:")
        print(clean_text[:800])
        sys.exit(1)

    if uganda is None:
        print("FATAL: could not parse Uganda case/death numbers from ECDC page.")
        print("DRC parsed fine so page loaded. Uganda sentence may have changed wording.")
        # Print a targeted slice around 'Uganda' to see the exact sentence
        idx = clean_text.lower().find('uganda had') 
        if idx == -1:
            idx = clean_text.lower().find('uganda')
        if idx != -1:
            print(f"Text around 'Uganda' (chars {idx-50} to {idx+200}):")
            print(repr(clean_text[max(0,idx-50):idx+200]))
        else:
            print("'Uganda' not found anywhere in clean text — page may be incomplete.")
            print("First 1000 chars:", clean_text[:1000])
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
    dashboard_data["summary"]["dataSource"] = "ECDC"

    dashboard_data["timeline"] = update_timeline(
        dashboard_data["timeline"], updated_date, total_cases, total_deaths
    )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, indent=2)

    print(f"Success: wrote summary + timeline to {JSON_FILE}")
    return dashboard_data


if __name__ == "__main__":
    scrape_ebola_data()
