"""
scrape.py - Ebola outbreak data scraper (ECDC source)
Uses Gemini Flash to extract case/death numbers from the ECDC page.
Run: python scrape.py
Requires: pip install requests cloudscraper google-genai
"""

import os
import re
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

ECDC_URL  = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"
JSON_FILE = Path(__file__).parent / "data.json"


# ── Page fetch ─────────────────────────────────────────────────────────────────

def fetch_page():
    try:
        import cloudscraper
    except ImportError:
        print("FATAL: cloudscraper not installed. Run: pip install cloudscraper")
        sys.exit(1)
    print("Fetching ECDC via cloudscraper...")
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        r = scraper.get(ECDC_URL, timeout=30)
        r.raise_for_status()
        print(f"  {len(r.text):,} chars, status {r.status_code}.")
        return r.text
    except Exception as e:
        print(f"FATAL: page fetch failed: {e}")
        sys.exit(1)


def to_clean_text(html):
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                        flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r'<[^>]+>', ' ', no_scripts)
    no_tags = no_tags.replace('\xa0', ' ').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', no_tags).strip()


def get_content_excerpt(clean_text, max_chars=3000):
    for marker in ["As of", "reported a total", "confirmed cases"]:
        idx = clean_text.find(marker)
        if idx != -1:
            return clean_text[max(0, idx - 100): idx + max_chars]
    return clean_text[:max_chars]


# ── Gemini extraction ──────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """Extract the CURRENT CUMULATIVE outbreak totals from this ECDC page text.
Return ONLY valid JSON, no explanation, no markdown:

{
  "drc_cases": <integer>,
  "drc_deaths": <integer>,
  "uganda_cases": <integer>,
  "uganda_deaths": <integer>,
  "updated_date": "<YYYY-MM-DD>"
}

Rules:
- CUMULATIVE totals only, not daily new cases.
- Convert word numbers to integers (e.g. "two" → 2).
- Numbers may use spaces as thousand separators (e.g. "1 759" = 1759).
- updated_date = the date the data refers to (not today's date).
- If a value is not found in the text, use null.

PAGE TEXT:
"""


def extract_with_gemini(page_excerpt):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("FATAL: GEMINI_API_KEY environment variable not set.")
        print("Get a free key at https://aistudio.google.com")
        print("Add it to GitHub: Settings → Secrets → Actions → GEMINI_API_KEY")
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        print("FATAL: google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    print("Extracting numbers via Gemini Flash...")
    client = genai.Client(api_key=api_key)

    max_retries = 3
    retry_delay = 15

    response_text = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-1.5-flash-latest",
                contents=EXTRACTION_PROMPT + page_excerpt,
            )
            response_text = response.text.strip()
            break
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"  Rate limited. Retrying in {retry_delay}s... ({attempt+1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                print(f"FATAL: Gemini API call failed: {e}")
                sys.exit(1)

    if response_text is None:
        print("FATAL: Gemini did not return a response after retries.")
        sys.exit(1)

    print(f"  Gemini response: {response_text}")

    # Parse JSON — strip markdown fences if Gemini added them
    try:
        clean = re.sub(r'```(?:json)?\s*', '', response_text).strip()
        data = json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', response_text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                print(f"FATAL: could not parse Gemini JSON: {response_text}")
                sys.exit(1)
        else:
            print(f"FATAL: Gemini returned non-JSON: {response_text}")
            sys.exit(1)

    # Validate required fields
    for key in ["drc_cases", "drc_deaths", "uganda_cases", "uganda_deaths"]:
        if data.get(key) is None:
            print(f"FATAL: Gemini could not extract '{key}' from the page.")
            sys.exit(1)

    return data


# ── Data persistence ───────────────────────────────────────────────────────────

def load_existing_data():
    baseline = {
        "updated": None,
        "summary": {},
        "timeline": [],
        "events": [],
        "healthZones": [],
        "network": {},
    }
    if not JSON_FILE.exists():
        return baseline
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception as e:
        print(f"WARNING: data.json unreadable ({e}). Starting fresh.")
        return baseline
    if not isinstance(loaded, dict):
        return baseline
    for key in ("summary", "timeline", "events", "healthZones", "network"):
        loaded.setdefault(key, baseline[key])
    return loaded


def update_timeline(timeline, date_str, cases, deaths):
    existing = next((p for p in timeline if p.get("date") == date_str), None)
    if existing:
        if existing.get("cases") == cases and existing.get("deaths") == deaths:
            print(f"Timeline entry for {date_str} unchanged.")
        else:
            existing["cases"]  = cases
            existing["deaths"] = deaths
            print(f"Updated timeline entry for {date_str}.")
    else:
        timeline.append({"date": date_str, "cases": cases, "deaths": deaths})
        print(f"Added timeline entry for {date_str}.")
    timeline.sort(key=lambda x: x["date"])
    return timeline


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape_ebola_data():
    print("=" * 42)
    print("Ebola Scraper — ECDC + Gemini extraction")
    print("=" * 42)

    html      = fetch_page()
    clean     = to_clean_text(html)
    excerpt   = get_content_excerpt(clean)

    # Debug: show what section Gemini will see
    print(f"PARSE DEBUG — excerpt sent to Gemini:\n  '{excerpt[:300]}'")

    extracted = extract_with_gemini(excerpt)

    drc_cases  = int(extracted["drc_cases"])
    drc_deaths = int(extracted["drc_deaths"])
    ug_cases   = int(extracted["uganda_cases"])
    ug_deaths  = int(extracted["uganda_deaths"])
    updated    = extracted.get("updated_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_cases  = drc_cases + ug_cases
    total_deaths = drc_deaths + ug_deaths

    if total_cases == 0:
        print("FATAL: extracted zero total cases — likely a blocked/empty page.")
        sys.exit(1)

    cfr = round(100 * total_deaths / total_cases, 1)
    print(f"DRC   : {drc_cases:,} cases / {drc_deaths:,} deaths")
    print(f"Uganda: {ug_cases:,} cases / {ug_deaths:,} deaths")
    print(f"CFR   : {cfr}% | as of {updated}")

    data = load_existing_data()
    data["updated"] = updated

    sm = data["summary"]
    sm["confirmedDRC"]    = drc_cases
    sm["confirmedDeaths"] = drc_deaths
    sm["ugandaCases"]     = ug_cases
    sm["ugandaDeaths"]    = ug_deaths
    sm["cfrPercent"]      = cfr
    sm["dataSource"]      = "ECDC"

    # Update network node figures for bunia and uganda
    for node in data.get("network", {}).get("nodes", []):
        if node["id"] == "bunia":
            node["cases"]  = drc_cases
            node["deaths"] = drc_deaths
            node["detail"] = f"Epicentre hub · {drc_cases:,} confirmed cases · {drc_deaths:,} deaths"
        if node["id"] == "uganda":
            node["cases"]  = ug_cases
            node["deaths"] = ug_deaths
            node["detail"] = f"{ug_cases} confirmed cases · {ug_deaths} death · Kampala · all linked to DRC travellers"

    data["timeline"] = update_timeline(
        data["timeline"], updated, total_cases, total_deaths
    )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Success — data.json updated ({updated})")
    return data


if __name__ == "__main__":
    scrape_ebola_data()