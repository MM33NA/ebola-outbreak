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
    suspected = extract_number(r"(\d[\d,]*)\s+suspected cases", text)
    if suspected == 0:
        suspected = extract_number(r"DRC[:\s]+(?:A total of\s+)?(\d[\d,]*)\s+suspected", text)

    # ── Confirmed cases ───────────────────────────────────────────────────
    confirmed = extract_number(r"(\d[\d,]*)\s+confirmed cases", text)
    if confirmed == 0:
        confirmed = extract_number(r"DRC[:\s]+(?:A total of\s+)?(\d[\d,]*)\s+confirmed", text)

    # ── Suspected deaths ──────────────────────────────────────────────────
    suspected_deaths = extract_number(r"(\d[\d,]*)\s+suspected deaths", text)
    if suspected_deaths == 0:
        suspected_deaths = extract_number(r"(?:DRC[:\s]+)?(\d[\d,]*)\s+suspected\s+deaths?", text)

    # ── Confirmed deaths ──────────────────────────────────────────────────
    confirmed_deaths = extract_number(r"(\d[\d,]*)\s+confirmed deaths", text)
    if confirmed_deaths == 0:
        confirmed_deaths = extract_number(r"(?:DRC[:\s]+)?(\d[\d,]*)\s+confirmed\s+deaths?", text)

    # ── Uganda cases ──────────────────────────────────────────────────────
    uganda_cases = extract_number(r"Uganda[:\s]+(?:A total of\s+)?(\d+)\s+confirmed cases", text)
    if uganda_cases == 0:
        uganda_cases = extract_number(r"(\d+)\s+cases?.{0,80}reported in Uganda", text)
    if uganda_cases == 0:
        words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
        m = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+cases?.{0,80}Uganda", text, re.IGNORECASE)
        if m:
            uganda_cases = words.get(m.group(1).lower(), 0)

    # ── Uganda deaths ─────────────────────────────────────────────────────
    uganda_deaths = extract_number(
        r"Uganda[:\s]+(?:A total of\s+)?\d+\s+confirmed cases\s+(?:and\s+)?(\d+)\s+confirmed death", text
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

    print(f"Syncing data.json with dashboard visual chart components...")

    # ── Update summary metrics safely ─────────────────────────────────────
    if s["suspected"]        > 0: current["suspectedCases"]   = s["suspected"]
    if s["suspected_deaths"] > 0: current["suspectedDeaths"]  = s["suspected_deaths"]
    if s["confirmed_deaths"] > 0: current["confirmedDeaths"]  = s["confirmed_deaths"]
    if s["uganda_cases"]     > 0: current["ugandaCases"]      = s["uganda_cases"]
    if s["uganda_deaths"]    > 0: current["ugandaDeaths"]     = s["uganda_deaths"]

    # Map the combined confirmed total safely to the key your KPI card reads
    if s["confirmed"] > 0:
        current["confirmedDRC"] = s["confirmed"] + s["uganda_cases"]

    # Calculate CFR based on suspected numbers
    if current["suspectedCases"] > 0:
        current["cfrPercent"] = round((current["suspectedDeaths"] / current["suspectedCases"]) * 100)

    data["updated"] = s["updated"]

    # ── Map Timeline Labels ──────────────────────────────────────────────
    try:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")

    existing = {p["date"]: i for i, p in enumerate(data["timeline"])}
    
    # CALCULATE VALUE FOR LINE CHART (Confirmed Total: 125 DRC + 7 Uganda = 132)
    total_confirmed = s["confirmed"] + s["uganda_cases"] if s["confirmed"] > 0 else current["confirmedDRC"]
    
    # If you prefer to keep suspected cases but want to stop the dip (Option 2), 
    # uncomment the line below and comment out the total_confirmed assignment above:
    # total_confirmed = max(s["suspected"], max([p["cases"] for p in data["timeline"]]) if data["timeline"] else 0)

    total_deaths = s["confirmed_deaths"] + s["uganda_deaths"] if s["confirmed_deaths"] > 0 else (current["confirmedDeaths"] + current["ugandaDeaths"])
    
    drc_confirmed = s["confirmed"] if s["confirmed"] > 0 else (current["confirmedDRC"] - current["ugandaCases"])
    uganda_confirmed = s["uganda_cases"] if s["uganda_cases"] > 0 else current["ugandaCases"]

    # Append structural keys so chart engines don't throw null errors
    if label in existing:
        idx = existing[label]
        data["timeline"][idx]["cases"] = total_confirmed
        data["timeline"][idx]["deaths"] = total_deaths
        data["timeline"][idx]["drcConfirmed"] = drc_confirmed
        data["timeline"][idx]["ugandaConfirmed"] = uganda_confirmed
    else:
        data["timeline"].append({
            "date": label,
            "cases": total_confirmed,
            "deaths": total_deaths,
            "drcConfirmed": drc_confirmed,
            "ugandaConfirmed": uganda_confirmed
        })

    # ── Update network nodes ──────────────────────────────────────────────
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

    print(f"data.json updated completely -> {s['updated']}")
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