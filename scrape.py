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

CDC_URL   = "https://www.cdc.gov/ebola/situation-summary/index.html"
DATA_FILE = Path(__file__).parent / "data.json"
HEADERS   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def extract_number(pattern, text, default=0):
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        for g in m.groups():
            if g is not None:
                try:
                    return int(g.replace(",", ""))
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
        print(f"ERROR fetching page: {e}")
        return None

    text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
    print(f"Page fetched — {len(text):,} chars")

    # ── Suspected cases ───────────────────────────────────────────────────
    # CDC: "DRC: A total of 904 suspected cases"
    suspected = extract_number(r"(\d[\d,]*)\s+suspected cases", text)

    # ── Confirmed cases ───────────────────────────────────────────────────
    # CDC: "101 confirmed cases"
    confirmed = extract_number(r"(\d[\d,]*)\s+confirmed cases", text)

    # ── Suspected deaths ──────────────────────────────────────────────────
    # CDC: "119 suspected deaths" or "906 suspected cases (223 deaths)"
    suspected_deaths = extract_number(r"(\d[\d,]*)\s+suspected deaths", text)
    if suspected_deaths == 0:
        suspected_deaths = extract_number(r"suspected cases\s*\(\s*(\d[\d,]*)\s+deaths?\s*\)", text)

    # ── Confirmed deaths ──────────────────────────────────────────────────
    # CDC: "10 confirmed deaths" or "105 confirmed cases (10 deaths)"
    confirmed_deaths = extract_number(r"(\d[\d,]*)\s+confirmed deaths", text)
    if confirmed_deaths == 0:
        confirmed_deaths = extract_number(r"confirmed cases\s*\(\s*(\d[\d,]*)\s+deaths?\s*\)", text)

    # ── Uganda cases ──────────────────────────────────────────────────────
    # CDC: Supports "A total of 5 confirmed cases" or "Uganda: 7 confirmed cases"
    uganda_cases = extract_number(r"Uganda[:\s]+A total of (\d+) confirmed cases", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"Uganda[:\s]+(?:A total of\s+)?(\d+)\s+(?:confirmed\s+)?cases?", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"(\d+)\s+cases?.{0,80}reported in Uganda", text)
    # Handle written numbers e.g. "Five cases ... Uganda"
    if uganda_cases == 0:
        words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
        m = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+cases?.{0,80}Uganda", text, re.IGNORECASE)
        if m:
            uganda_cases = words.get(m.group(1).lower(), 0)

    # ── Uganda deaths ─────────────────────────────────────────────────────
    # CDC: Matches "1 confirmed death" or "1 death"
    uganda_deaths = extract_number(
        r"Uganda[:\s]+A total of \d+ confirmed cases and (\d+) confirmed death", text
    )
    if uganda_deaths == 0:
        uganda_deaths = extract_number(r"Uganda.{0,120}(\d+)\s+(?:confirmed\s+)?deaths?", text)

    # ── Update date ───────────────────────────────────────────────────────
    m = re.search(r"As of\s+([A-Z][a-z]+ \d{1,2},?\s*\d{4})", text, re.IGNORECASE)
    try:
        updated = datetime.strptime(
            m.group(1).replace(",", "").strip(), "%B %d %Y"
        ).strftime("%Y-%m-%d") if m else today_str()
    except Exception:
        updated = today_str()

    result = dict(
        suspected=suspected,
        confirmed=confirmed,
        suspected_deaths=suspected_deaths,
        confirmed_deaths=confirmed_deaths,
        uganda_cases=uganda_cases,
        uganda_deaths=uganda_deaths,
        updated=updated
    )
    print(f"Extracted: {result}")
    return result


def update_json(s):
    if not DATA_FILE.exists():
        print("ERROR: data.json not found")
        return False

    with DATA_FILE.open() as f:
        data = json.load(f)

    current = data["summary"]

    # Update if ANY trackable numbers changed OR date changed
    numbers_changed = (
        (s["suspected"]       > 0 and s["suspected"]       != current.get("suspectedCases", 0)) or
        (s["suspected_deaths"]> 0 and s["suspected_deaths"] != current.get("suspectedDeaths", 0)) or
        (s["confirmed"]       > 0 and s["confirmed"]        != current.get("confirmedDRC", 0)) or
        (s["confirmed_deaths"]> 0 and s["confirmed_deaths"] != current.get("confirmedDeaths", 0)) or
        (s["uganda_cases"]    > 0 and s["uganda_cases"]    != current.get("ugandaCases", 0)) or
        (s["uganda_deaths"]   > 0 and s["uganda_deaths"]   != current.get("ugandaDeaths", 0))
    )
    date_changed = data.get("updated") != s["updated"]

    if not numbers_changed and not date_changed:
        print(f"No changes detected. Cases: {s['suspected']}  Deaths: {s['suspected_deaths']}. Skipping.")
        return False

    print(f"Changes detected — updating data.json")

    # ── Update summary ────────────────────────────────────────────────────
    if s["suspected"]        > 0: current["suspectedCases"]   = s["suspected"]
    if s["suspected_deaths"] > 0: current["suspectedDeaths"]  = s["suspected_deaths"]
    if s["confirmed"]        > 0: current["confirmedDRC"]     = s["confirmed"]
    if s["confirmed_deaths"] > 0: current["confirmedDeaths"]  = s["confirmed_deaths"]
    if s["uganda_cases"]     > 0: current["ugandaCases"]      = s["uganda_cases"]
    if s["uganda_deaths"]    > 0: current["ugandaDeaths"]     = s["uganda_deaths"]

    if current["suspectedCases"] > 0:
        current["cfrPercent"] = round(current["suspectedDeaths"] / current["suspectedCases"] * 100)

    data["updated"] = s["updated"]

    # ── Append or update timeline point ──────────────────────────────────
    try:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")

    existing = {p["date"]: i for i, p in enumerate(data["timeline"])}
    if label in existing:
        data["timeline"][existing[label]]["cases"]  = current["suspectedCases"]
        data["timeline"][existing[label]]["deaths"] = current["suspectedDeaths"]
        print(f"Timeline point updated: {label}")
    else:
        data["timeline"].append({
            "date":   label,
            "cases":  current["suspectedCases"],
            "deaths": current["suspectedDeaths"],
        })
        print(f"Timeline point added: {label}")

    # ── Update network nodes ──────────────────────────────────────────────
    for node in data.get("network", {}).get("nodes", []):
        if node["id"] == "uganda":
            node["cases"]  = current["ugandaCases"]
            node["deaths"] = current["ugandaDeaths"]
            node["detail"] = (
                f"{current['ugandaCases']} confirmed cases · "
                f"{current['ugandaDeaths']} death · Kampala · all linked to DRC travellers"
            )
        if node["id"] == "bunia":
            node["cases"]  = current["suspectedCases"]
            node["deaths"] = current["suspectedDeaths"]
            node["detail"] = (
                f"Epicentre hub · {current['suspectedCases']} suspected cases · "
                f"{current['suspectedDeaths']} suspected deaths · 3 provinces confirmed"
            )

    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"data.json updated -> {s['updated']}")
    print(f"Suspected: {current['suspectedCases']} cases  {current['suspectedDeaths']} deaths")
    print(f"Confirmed: {current['confirmedDRC']} cases  {current.get('confirmedDeaths', '?')} deaths")
    print(f"Uganda: {current['ugandaCases']} cases  {current['ugandaDeaths']} deaths")
    return True


def main():
    print("=" * 40)
    print(f"Ebola scraper — {today_str()}")
    print("=" * 40)

    scraped = scrape_cdc()
    if not scraped:
        sys.exit(1)

    if scraped["suspected"] == 0 and scraped["suspected_deaths"] == 0:
        print("WARNING: All zeros — CDC page structure may have changed.")
        print("Check scrape.py regex patterns.")
        sys.exit(1)

    update_json(scraped)
    print("Done.")


if __name__ == "__main__":
    main()