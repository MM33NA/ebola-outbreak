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

    # ── Text Processing Fallbacks (Optimized for May 29 Layout) ───────────
    suspected = extract_number(r"(\d[\d,]*)\s+suspected cases", text)
    if suspected == 0:
        suspected = extract_number(r"DRC[:\s]+(?:A total of\s+)?(\d[\d,]*)\s+suspected", text)

    confirmed = extract_number(r"(\d[\d,]*)\s+confirmed cases", text)
    if confirmed == 0:
        confirmed = extract_number(r"DRC[:\s]+(?:A total of\s+)?(\d[\d,]*)\s+confirmed", text)

    suspected_deaths = extract_number(r"(\d[\d,]*)\s+suspected deaths", text)
    if suspected_deaths == 0:
        suspected_deaths = extract_number(r"(?:DRC[:\s]+)?(\d[\d,]*)\s+suspected\s+deaths?", text)

    confirmed_deaths = extract_number(r"(\d[\d,]*)\s+confirmed deaths", text)
    if confirmed_deaths == 0:
        confirmed_deaths = extract_number(r"(?:DRC[:\s]+)?(\d[\d,]*)\s+confirmed\s+deaths?", text)

    uganda_cases = extract_number(r"Uganda[:\s]+(?:A total of\s+)?(\d+)\s+confirmed cases", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"(\d+)\s+cases?.{0,80}reported in Uganda", text)

    uganda_deaths = extract_number(r"Uganda[:\s]+(?:A total of\s+)?\d+\s+confirmed cases\s+(?:and\s+)?(\d+)\s+confirmed death", text)
    if uganda_deaths == 0:
        uganda_deaths = extract_number(r"Uganda.{0,120}(\d+)\s+(?:confirmed\s+)?deaths?", text)

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

    print("Overhauling pipeline integration details...")

    # ── 1. Update Global KPI Summary Blocks ───────────────────────────────
    if s["suspected"]        > 0: current["suspectedCases"]   = s["suspected"]
    if s["suspected_deaths"] > 0: current["suspectedDeaths"]  = s["suspected_deaths"]
    if s["confirmed_deaths"] > 0: current["confirmedDeaths"]  = s["confirmed_deaths"]
    if s["uganda_cases"]     > 0: current["ugandaCases"]      = s["uganda_cases"]
    if s["uganda_deaths"]    > 0: current["ugandaDeaths"]     = s["uganda_deaths"]

    # Synchronize the main Lab Confirmed card to display 132 (125 + 7)
    if s["confirmed"] > 0:
        current["confirmedDRC"] = s["confirmed"] + s["uganda_cases"]

    if current["suspectedCases"] > 0:
        current["cfrPercent"] = round((current["suspectedDeaths"] / current["suspectedCases"]) * 100)

    data["updated"] = s["updated"]

    # ── 2. Format Timeline Entries Without Chart Drops ───────────────────
    try:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")

    # OPTION 2 LOGIC: Prevent cumulative charts from dipping backwards if data gets reclassified
    historical_cases_peak = max([p["cases"] for p in data["timeline"]]) if data["timeline"] else 0
    historical_deaths_peak = max([p["deaths"] for p in data["timeline"]]) if data["timeline"] else 0

    cases_value = max(s["suspected"] if s["suspected"] > 0 else current["suspectedCases"], historical_cases_peak)
    deaths_value = max(s["suspected_deaths"] if s["suspected_deaths"] > 0 else current["suspectedDeaths"], historical_deaths_peak)

    # Segments dedicated specifically to mapping your Stacked Bar Chart values
    drc_seg = s["confirmed"] if s["confirmed"] > 0 else (current["confirmedDRC"] - current["ugandaCases"])
    uganda_seg = s["uganda_cases"] if s["uganda_cases"] > 0 else current["ugandaCases"]

    existing = {p["date"]: i for i, p in enumerate(data["timeline"])}
    if label in existing:
        idx = existing[label]
        data["timeline"][idx]["cases"] = cases_value
        data["timeline"][idx]["deaths"] = deaths_value
        data["timeline"][idx]["drcConfirmed"] = drc_seg
        data["timeline"][idx]["ugandaConfirmed"] = uganda_seg
    else:
        data["timeline"].append({
            "date": label,
            "cases": cases_value,
            "deaths": deaths_value,
            "drcConfirmed": drc_seg,
            "ugandaConfirmed": uganda_seg
        })

    # ── 3. Map Node Configurations ────────────────────────────────────────
    for node in data.get("network", {}).get("nodes", []):
        if node["id"] == "uganda":
            node["cases"]  = current["ugandaCases"]
            node["deaths"] = current["ugandaDeaths"]
            node["detail"] = (
                f"{current['ugandaCases']} confirmed cases · "
                f"{current['ugandaDeaths']} confirmed death · Kampala · "
                f"5 linked to first 2 cases"
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

    print(f"data.json successfully saved and streamlined for all charts.")
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
        sys.exit(1)

    update_json(scraped)
    print("Done.")


if __name__ == "__main__":
    main()