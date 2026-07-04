"""
scrape.py - Ebola outbreak data scraper (ECDC source)

Source: ECDC outbreak page, fetched via cloudscraper (handles Cloudflare).
The ECDC page wording has changed multiple times during this outbreak.
All known sentence patterns are handled by parse_drc() and parse_uganda().

Run: python scrape.py
Requires: pip install requests cloudscraper pytrends
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
    try:
        import cloudscraper
    except ImportError:
        print("FATAL: cloudscraper not installed. Run: pip install cloudscraper")
        sys.exit(1)
    print(f"Fetching ECDC via cloudscraper...")
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        r = scraper.get(ECDC_URL, timeout=30)
        r.raise_for_status()
        print(f"  {len(r.text):,} chars, status {r.status_code}.")
        return r.text
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)


def to_clean_text(html):
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
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
        return datetime.strptime(f"{day_month} {year}", "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_drc(clean_text):
    """
    All confirmed ECDC sentence patterns for DRC:

    PATTERN A (pre-July 2026):
      "DRC Ministry of Health reported a total of 1 155 confirmed cases,
       including 304 confirmed related deaths"

    PATTERN B (July 3 2026 onward — split sentence):
      "Democratic Republic of the Congo (DRC) reported a total of 1 460
       confirmed cases (from data up until 30 June)...
       A total 452 related deaths have been confirmed so far."

    PATTERN C (variant seen in search snippet):
      "National Institute of Public Health reported a total of 1 333
       confirmed cases and 399 total related deaths"
    """
    # Pattern A: inline "X cases, including Y deaths"
    m = re.search(
        r'(?:DRC Ministry of Health|DRC)\s+(?:Ministry of Health\s+)?'
        r'reported\s+a\s+total\s+of\s+([\d ,]+?)\s+confirmed\s+cases'
        r'\s*,\s*including\s+([\d ,]+?)\s+confirmed\s+related\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern B: split sentence — cases first, then "A total X related deaths"
    m = re.search(
        r'DRC\)\s+reported\s+a\s+total\s+of\s+([\d ,]+?)\s+confirmed\s+cases'
        r'.*?A\s+total\s+([\d ,]+?)\s+related\s+deaths',
        clean_text, re.IGNORECASE | re.DOTALL
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern C: "National Institute" variant
    m = re.search(
        r'National Institute of Public Health\s+reported\s+a\s+total\s+of'
        r'\s+([\d ,]+?)\s+confirmed\s+cases\s+and\s+([\d ,]+?)\s+total\s+related\s+deaths',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    return None


def parse_uganda(clean_text):
    """
    All confirmed ECDC sentence patterns for Uganda:

    PATTERN A (pre-July 2026):
      "Uganda had reported a total of 20 confirmed cases, including two deaths"

    PATTERN B (July 3 2026 onward):
      "a total of 20 confirmed cases, including two deaths, have been
       reported by the Ministry of Health in Uganda"
    """
    # Pattern A: "Uganda had/has reported ... X cases, including Y deaths"
    m = re.search(
        r'Uganda\s+(?:had\s+|has\s+)?reported(?:\s+a\s+total\s+of)?\s+([\d ,]+?)\s+confirmed\s+cases'
        r',?\s+including\s+(\w+)\s+deaths?',
        clean_text, re.IGNORECASE
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    # Pattern B: "total of X confirmed cases, including Y deaths ... Uganda"
    m = re.search(
        r'total\s+of\s+([\d ,]+?)\s+confirmed\s+cases,\s+including\s+(\w+)\s+deaths'
        r'.*?Ministry\s+of\s+Health\s+in\s+Uganda',
        clean_text, re.IGNORECASE | re.DOTALL
    )
    if m:
        return {"cases": parse_int_token(m.group(1)), "deaths": parse_int_token(m.group(2))}

    return None


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
    for k in ("suspected", "confirmed", "suspected_deaths",
              "confirmed_deaths", "uganda_cases", "uganda_deaths"):
        loaded.pop(k, None)
    return loaded


def update_timeline(timeline, date_str, cases, deaths):
    existing = next((p for p in timeline if p.get("date") == date_str), None)
    if existing:
        if existing.get("cases") == cases and existing.get("deaths") == deaths:
            print(f"Timeline entry for {date_str} unchanged.")
        else:
            existing["cases"] = cases
            existing["deaths"] = deaths
            print(f"Updated timeline entry for {date_str}.")
    else:
        timeline.append({"date": date_str, "cases": cases, "deaths": deaths})
        print(f"Added timeline entry for {date_str}.")
    timeline.sort(key=lambda x: x["date"])
    return timeline


def scrape_ebola_data():
    print("========================================")
    print("Ebola Scraper — ECDC via cloudscraper")
    print("========================================")

    html = fetch_page()
    clean = to_clean_text(html)

    updated_date = parse_last_updated(clean)
    drc = parse_drc(clean)
    uganda = parse_uganda(clean)

    # Debug: show the relevant section if parsing fails
    if drc is None or uganda is None:
        # Find the main content section (skip navigation)
        idx = clean.find("reported a total of")
        snippet = clean[max(0, idx-100):idx+400] if idx != -1 else clean[1000:1800]
        print("PARSE DEBUG — relevant page section:")
        print(repr(snippet))

    if drc is None:
        print("FATAL: could not parse DRC numbers. See debug snippet above.")
        sys.exit(1)
    if uganda is None:
        print("FATAL: could not parse Uganda numbers. See debug snippet above.")
        sys.exit(1)

    total_cases = drc["cases"] + uganda["cases"]
    total_deaths = drc["deaths"] + uganda["deaths"]

    if total_cases == 0:
        print("FATAL: parsed zeros — treating as parse failure.")
        sys.exit(1)

    cfr = round(100 * total_deaths / total_cases, 1)
    print(f"DRC: {drc['cases']} cases / {drc['deaths']} deaths | "
          f"Uganda: {uganda['cases']} cases / {uganda['deaths']} deaths | "
          f"CFR: {cfr}% | as of {updated_date}")

    data = load_existing_data()
    data["updated"] = updated_date
    data["summary"]["confirmedDRC"] = drc["cases"]
    data["summary"]["confirmedDeaths"] = drc["deaths"]
    data["summary"]["ugandaCases"] = uganda["cases"]
    data["summary"]["ugandaDeaths"] = uganda["deaths"]
    data["summary"]["cfrPercent"] = cfr
    data["summary"]["dataSource"] = "ECDC"
    data["timeline"] = update_timeline(data["timeline"], updated_date, total_cases, total_deaths)

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Success: wrote to {JSON_FILE}")
    return data


if __name__ == "__main__":
    scrape_ebola_data()
