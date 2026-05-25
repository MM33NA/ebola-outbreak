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
    # CDC format: "904 suspected cases" or "reports of 575 suspected cases"
    suspected = extract_number(r"(\d[\d,]*)\s+suspected cases", text)

    # ── Confirmed cases ───────────────────────────────────────────────────
    # CDC format: "83 confirmed cases" or "8 laboratory-confirmed cases"
    confirmed = extract_number(r"(\d[\d,]*)\s+(?:laboratory-)?confirmed cases", text)

    # ── Deaths ────────────────────────────────────────────────────────────
    # CDC format: "176 suspected deaths"
    deaths = extract_number(r"(\d[\d,]*)\s+suspected deaths", text)

    # ── Uganda cases ──────────────────────────────────────────────────────
    # CDC format (May 24+): "Five cases related to the DRC outbreak also have been reported in Uganda"
    # or: "Uganda: A total of 5 confirmed cases"
    uganda_cases = extract_number(r"Uganda[:\s]+A total of (\d+) confirmed cases", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"(\d+)\s+cases?.{0,80}reported in Uganda", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"(\d+)\s+confirmed cases.{0,40}Uganda", text)
    # CDC sometimes writes "Five cases" in words — handle that
    if uganda_cases == 0:
        words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
        m = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+cases?.{0,80}Uganda", text, re.IGNORECASE)
        if m:
            uganda_cases = words.get(m.group(1).lower(), 0)

    # ── Uganda deaths ─────────────────────────────────────────────────────
    uganda_deaths = extract_number(
        r"Uganda[:\s]+A total of \d+ confirmed cases and (\d+) confirmed death", text
    )
    if uganda_deaths == 0:
        uganda_deaths = extract_number(r"Uganda.{0,120}(\d+)\s+(?:confirmed\s+)?death", text)

    # ── Update date ───────────────────────────────────────────────────────
    # FIX: only match "As of May 24, 2026" — require "As of" prefix to avoid
    # picking up metadata dates earlier in the page
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
        deaths=deaths,
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

    # FIX: compare numbers not just date — update if numbers changed even if date is same
    current = data["summary"]
    numbers_changed = (
        s["suspected"] > 0 and s["suspected"] != current["suspectedCases"] or
        s["deaths"]    > 0 and s["deaths"]    != current["suspectedDeaths"]
    )
    date_changed = data.get("updated") != s["updated"]

    if not numbers_changed and not date_changed:
        print(f"No changes detected. Cases: {s['suspected']}  Deaths: {s['deaths']}. Skipping.")
        return False

    print(f"Changes detected — updating data.json")

    # ── Update summary ────────────────────────────────────────────────────
    if s["suspected"]     > 0: current["suspectedCases"]  = s["suspected"]
    if s["deaths"]        > 0: current["suspectedDeaths"] = s["deaths"]
    if s["confirmed"]     > 0: current["confirmedDRC"]    = s["confirmed"]
    if s["uganda_cases"]  > 0: current["ugandaCases"]     = s["uganda_cases"]
    if s["uganda_deaths"] > 0: current["ugandaDeaths"]    = s["uganda_deaths"]

    if current["suspectedCases"] > 0:
        current["cfrPercent"] = round(current["suspectedDeaths"] / current["suspectedCases"] * 100)

    data["updated"] = s["updated"]

    # ── Append timeline point ─────────────────────────────────────────────
    try:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")

    existing = {p["date"] for p in data["timeline"]}
    if label not in existing:
        data["timeline"].append({
            "date":   label,
            "cases":  current["suspectedCases"],
            "deaths": current["suspectedDeaths"],
        })
        print(f"Timeline point added: {label}")
    else:
        # Update existing point with latest numbers
        for pt in data["timeline"]:
            if pt["date"] == label:
                pt["cases"]  = current["suspectedCases"]
                pt["deaths"] = current["suspectedDeaths"]
                print(f"Timeline point updated: {label}")

    # ── Update network nodes ──────────────────────────────────────────────
    for node in data.get("network", {}).get("nodes", []):
        if node["id"] == "uganda":
            node["cases"]  = current["ugandaCases"]
            node["detail"] = (
                f"{current['ugandaCases']} confirmed cases · "
                f"{current['ugandaDeaths']} death · Kampala · traced to DRC travellers"
            )
        if node["id"] == "bunia":
            node["cases"]  = current["suspectedCases"]
            node["detail"] = (
                f"Epicentre hub · {current['suspectedCases']} suspected cases · "
                f"{current['suspectedDeaths']} deaths · 3 provinces confirmed"
            )

    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"data.json updated -> {s['updated']}")
    print(f"Cases: {current['suspectedCases']}  Deaths: {current['suspectedDeaths']}  Uganda: {current['ugandaCases']}")
    return True


def main():
    print("=" * 40)
    print(f"Ebola scraper — {today_str()}")
    print("=" * 40)

    scraped = scrape_cdc()
    if not scraped:
        sys.exit(1)

    if scraped["suspected"] == 0 and scraped["deaths"] == 0:
        print("WARNING: All zeros — CDC page structure may have changed.")
        print("Check scrape.py regex patterns.")
        sys.exit(1)

    update_json(scraped)
    print("Done.")


if __name__ == "__main__":
    main()