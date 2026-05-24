"""
scrape.py — Ebola outbreak data scraper
Fetches latest figures from CDC situation summary page and updates data.json
Run manually: python scrape.py
Run automatically: via GitHub Actions (.github/workflows/update.yml)
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

CDC_URL = "https://www.cdc.gov/ebola/situation-summary/index.html"
DATA_FILE = Path(__file__).parent / "data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_number(pattern: str, text: str, default: int = 0) -> int:
    """Extract first integer matching a regex pattern from text."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        # Remove commas before converting e.g. "1,234" → 1234
        raw = m.group(1).replace(",", "")
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ── Scraper ───────────────────────────────────────────────────────────────────

def scrape_cdc() -> dict | None:
    """
    Scrape the CDC Ebola situation summary page.
    Returns a dict with keys: suspected_cases, confirmed_cases,
    suspected_deaths, uganda_cases, uganda_deaths, updated.
    Returns None on failure.
    """
    print(f"[scrape] Fetching {CDC_URL}")
    try:
        resp = requests.get(CDC_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[scrape] ERROR fetching CDC page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    # Grab all visible text in one block for regex matching
    text = soup.get_text(separator=" ", strip=True)

    print(f"[scrape] Page fetched — {len(text):,} chars")

    # ── Extract figures ────────────────────────────────────────────────────
    # Patterns based on CDC's current sentence structures e.g.:
    # "83 confirmed + 744 suspected cases = 827 total; 176 suspected deaths"
    # "5 cases related to the DRC outbreak also have been reported in Uganda"

    suspected = extract_number(
        r"(\d[\d,]*)\s+suspected cases", text
    )
    confirmed = extract_number(
        r"(\d[\d,]*)\s+confirmed(?:\s+\+\s+\d[\d,]*\s+suspected)?\s+cases", text
    )
    deaths = extract_number(
        r"(\d[\d,]*)\s+(?:suspected\s+)?deaths", text
    )
    uganda_cases = extract_number(
        r"(\d[\d,]*)\s+cases?\s+(?:related|reported|confirmed).{0,60}Uganda", text
    )
    uganda_deaths = extract_number(
        r"Uganda.{0,120}(\d)\s+death", text
    )

    # ── Extract update date ────────────────────────────────────────────────
    # CDC usually writes "As of May 23, 2026" or "Updated May 23, 2026"
    date_match = re.search(
        r"(?:As of|Updated?|Last updated?)[,:]?\s+([A-Z][a-z]+ \d{1,2},?\s+\d{4})",
        text, re.IGNORECASE
    )
    if date_match:
        try:
            parsed = datetime.strptime(
                date_match.group(1).replace(",", ""), "%B %d %Y"
            )
            updated = parsed.strftime("%Y-%m-%d")
        except ValueError:
            updated = today_str()
    else:
        updated = today_str()

    result = {
        "suspected_cases": suspected,
        "confirmed_cases":  confirmed,
        "suspected_deaths": deaths,
        "uganda_cases":     uganda_cases,
        "uganda_deaths":    uganda_deaths,
        "updated":          updated,
    }

    print(f"[scrape] Extracted: {result}")
    return result


# ── JSON updater ──────────────────────────────────────────────────────────────

def update_data_json(scraped: dict) -> bool:
    """
    Merge scraped figures into data.json.
    Appends a new timeline point only if the date is new.
    Returns True if data.json was changed, False if nothing new.
    """
    if not DATA_FILE.exists():
        print(f"[update] ERROR: {DATA_FILE} not found")
        return False

    with DATA_FILE.open() as f:
        data = json.load(f)

    # ── Check if we actually have new data ────────────────────────────────
    current_updated = data.get("updated", "")
    new_updated = scraped["updated"]

    if current_updated == new_updated:
        print(f"[update] No new data — date unchanged ({new_updated}). Skipping.")
        return False

    # ── Update summary ────────────────────────────────────────────────────
    s = data["summary"]
    s["suspectedCases"]  = scraped["suspected_cases"]  or s["suspectedCases"]
    s["suspectedDeaths"] = scraped["suspected_deaths"] or s["suspectedDeaths"]
    s["confirmedDRC"]    = scraped["confirmed_cases"]  or s["confirmedDRC"]
    s["ugandaCases"]     = scraped["uganda_cases"]     or s["ugandaCases"]
    s["ugandaDeaths"]    = scraped["uganda_deaths"]    or s["ugandaDeaths"]

    # Recalculate CFR
    if s["suspectedCases"] > 0:
        s["cfrPercent"] = round(
            (s["suspectedDeaths"] / s["suspectedCases"]) * 100
        )

    data["updated"] = new_updated

    # ── Append timeline point ─────────────────────────────────────────────
    existing_dates = {pt["date"] for pt in data["timeline"]}
    label = datetime.strptime(new_updated, "%Y-%m-%d").strftime("%b %-d")

    if label not in existing_dates:
        data["timeline"].append({
            "date":   label,
            "cases":  s["suspectedCases"],
            "deaths": s["suspectedDeaths"],
        })
        print(f"[update] Appended timeline point: {label}")

    # ── Write back ────────────────────────────────────────────────────────
    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"[update] data.json updated → {new_updated}")
    print(f"[update] Cases: {s['suspectedCases']}  Deaths: {s['suspectedDeaths']}")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"Ebola scraper — {today_str()}")
    print("=" * 50)

    scraped = scrape_cdc()

    if scraped is None:
        print("[main] Scrape failed. Exiting.")
        sys.exit(1)

    # Sanity check — if all zeros something went wrong with parsing
    if scraped["suspected_cases"] == 0 and scraped["suspected_deaths"] == 0:
        print("[main] WARNING: All figures are zero — page structure may have changed.")
        print("[main] Check CDC_URL and update regex patterns in scrape.py.")
        sys.exit(1)

    changed = update_data_json(scraped)

    if changed:
        print("[main] Done — data.json updated successfully.")
    else:
        print("[main] Done — no changes made.")


if __name__ == "__main__":
    main()
