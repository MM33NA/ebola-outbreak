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
        # Return first non-None group
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
    # e.g. "reports of 575 suspected cases"
    # or   "744 suspected cases"
    suspected = extract_number(
        r"(\d[\d,]*)\s+suspected cases", text
    )

    # ── Confirmed cases ───────────────────────────────────────────────────
    # e.g. "83 confirmed cases" or "8 laboratory-confirmed cases"
    confirmed = extract_number(
        r"(\d[\d,]*)\s+(?:laboratory-)?confirmed cases", text
    )

    # ── Deaths ────────────────────────────────────────────────────────────
    # e.g. "176 suspected deaths" or "148 suspected deaths"
    deaths = extract_number(
        r"(\d[\d,]*)\s+suspected deaths", text
    )

    # ── Uganda cases ──────────────────────────────────────────────────────
    # CDC format: "Uganda: A total of 5 confirmed cases and 1 confirmed death"
    # Also handles: "5 cases related to the DRC outbreak also have been reported in Uganda"
    uganda_cases = extract_number(
        r"Uganda[:\s]+A total of (\d+) confirmed cases", text
    )
    if uganda_cases == 0:
        uganda_cases = extract_number(
            r"(\d+)\s+cases?.{0,80}reported in Uganda", text
        )
    if uganda_cases == 0:
        uganda_cases = extract_number(
            r"(\d+)\s+confirmed cases.{0,40}Uganda", text
        )

    # ── Uganda deaths ─────────────────────────────────────────────────────
    # CDC format: "Uganda: A total of 5 confirmed cases and 1 confirmed death"
    uganda_deaths = extract_number(
        r"Uganda[:\s]+A total of \d+ confirmed cases and (\d+) confirmed death", text
    )
    if uganda_deaths == 0:
        uganda_deaths = extract_number(
            r"Uganda.{0,120}(\d+)\s+(?:confirmed\s+)?death", text
        )

    # ── Update date ───────────────────────────────────────────────────────
    # CDC writes "May 16, 2026" or "As of May 23, 2026"
    m = re.search(
        r"(?:As of\s+)?([A-Z][a-z]+ \d{1,2},\s*\d{4})",
        text, re.IGNORECASE
    )
    try:
        updated = datetime.strptime(
            m.group(1).replace(",", ""), "%B %d %Y"
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

    if data.get("updated") == s["updated"]:
        print(f"No new data ({s['updated']}). Skipping.")
        return False

    # ── Update summary ────────────────────────────────────────────────────
    sm = data["summary"]
    if s["suspected"]    > 0: sm["suspectedCases"]  = s["suspected"]
    if s["deaths"]       > 0: sm["suspectedDeaths"] = s["deaths"]
    if s["confirmed"]    > 0: sm["confirmedDRC"]    = s["confirmed"]
    if s["uganda_cases"] > 0: sm["ugandaCases"]     = s["uganda_cases"]
    if s["uganda_deaths"]> 0: sm["ugandaDeaths"]    = s["uganda_deaths"]

    if sm["suspectedCases"] > 0:
        sm["cfrPercent"] = round(sm["suspectedDeaths"] / sm["suspectedCases"] * 100)

    data["updated"] = s["updated"]

    # ── Append timeline point ─────────────────────────────────────────────
    try:
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        # Windows doesn't support %-d — use %d and strip leading zero
        label = datetime.strptime(s["updated"], "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")

    existing = {p["date"] for p in data["timeline"]}
    if label not in existing:
        data["timeline"].append({
            "date":   label,
            "cases":  sm["suspectedCases"],
            "deaths": sm["suspectedDeaths"],
        })
        print(f"Timeline point added: {label}")

    # ── Update network nodes ──────────────────────────────────────────────
    # Keep network node data in sync with summary
    for node in data.get("network", {}).get("nodes", []):
        if node["id"] == "uganda":
            node["cases"]  = sm["ugandaCases"]
            node["detail"] = (
                f"{sm['ugandaCases']} confirmed cases · "
                f"{sm['ugandaDeaths']} death · Kampala · traced to DRC travellers"
            )
        if node["id"] == "bunia":
            node["cases"]  = sm["suspectedCases"]
            node["detail"] = (
                f"Regional commercial hub · {sm['suspectedCases']} suspected cases · "
                f"{sm['suspectedDeaths']} deaths"
            )

    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"data.json updated -> {s['updated']}")
    print(f"Cases: {sm['suspectedCases']}  Deaths: {sm['suspectedDeaths']}  Uganda: {sm['ugandaCases']}")
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
