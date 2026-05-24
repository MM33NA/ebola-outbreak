"""
scrape.py - Ebola outbreak data scraper
Fetches latest figures from CDC and updates data.json
Run: python scrape.py
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CDC_URL  = "https://www.cdc.gov/ebola/situation-summary/index.html"
DATA_FILE = Path(__file__).parent / "data.json"
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def extract_number(pattern, text, default=0):
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return default


def today_str():
    return date.today().strftime("%Y-%m-%d")


def scrape_cdc():
    print(f"Fetching {CDC_URL} ...")
    try:
        resp = requests.get(CDC_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
    print(f"Page fetched — {len(text):,} chars")

    suspected = extract_number(r"(\d[\d,]*)\s+suspected cases", text)
    confirmed = extract_number(r"(\d[\d,]*)\s+confirmed cases", text)
    deaths    = extract_number(r"(\d[\d,]*)\s+(?:suspected\s+)?deaths", text)
    uganda    = extract_number(r"(\d[\d,]*)\s+cases?.{0,60}Uganda", text)
    u_deaths  = extract_number(r"Uganda.{0,120}(\d)\s+death", text)

    m = re.search(r"(?:As of|Updated?)[,:]?\s+([A-Z][a-z]+ \d{1,2},?\s+\d{4})", text, re.IGNORECASE)
    try:
        updated = datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y").strftime("%Y-%m-%d") if m else today_str()
    except Exception:
        updated = today_str()

    result = dict(suspected=suspected, confirmed=confirmed,
                  deaths=deaths, uganda=uganda, u_deaths=u_deaths, updated=updated)
    print(f"Extracted: {result}")
    return result


def update_json(s):
    if not DATA_FILE.exists():
        print("ERROR: data.json not found")
        return False

    with DATA_FILE.open() as f:
        data = json.load(f)

    if data.get("updated") == s["updated"]:
        print(f"No new data ({s['updated']}). Skipping.")
        return False

    data["summary"]["suspectedCases"]  = s["suspected"] or data["summary"]["suspectedCases"]
    data["summary"]["suspectedDeaths"] = s["deaths"]    or data["summary"]["suspectedDeaths"]
    data["summary"]["confirmedDRC"]    = s["confirmed"] or data["summary"]["confirmedDRC"]
    data["summary"]["ugandaCases"]     = s["uganda"]    or data["summary"]["ugandaCases"]
    data["summary"]["ugandaDeaths"]    = s["u_deaths"]  or data["summary"]["ugandaDeaths"]

    if data["summary"]["suspectedCases"] > 0:
        data["summary"]["cfrPercent"] = round(
            data["summary"]["suspectedDeaths"] / data["summary"]["suspectedCases"] * 100
        )

    data["updated"] = s["updated"]

    label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    existing = {p["date"] for p in data["timeline"]}
    if label not in existing:
        data["timeline"].append({
            "date":   label,
            "cases":  data["summary"]["suspectedCases"],
            "deaths": data["summary"]["suspectedDeaths"],
        })
        print(f"Timeline point added: {label}")

    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"data.json updated -> {s['updated']}")
    return True


def main():
    print("=" * 40)
    print(f"Ebola scraper — {today_str()}")
    print("=" * 40)

    scraped = scrape_cdc()
    if not scraped:
        sys.exit(1)

    if scraped["suspected"] == 0 and scraped["deaths"] == 0:
        print("WARNING: All zeros — CDC page structure may have changed. Check scrape.py patterns.")
        sys.exit(1)

    update_json(scraped)
    print("Done.")


if __name__ == "__main__":
    main()