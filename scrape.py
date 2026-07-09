"""
scrape.py - Ebola outbreak data scraper (ECDC source)

Uses Gemini Flash (free tier, no credit card) to extract case/death numbers
from the ECDC page, replacing brittle regex that broke every time ECDC
changed their sentence wording.

FREE TIER SETUP (2 minutes, no credit card):
  1. Go to https://aistudio.google.com
  2. Sign in with your Google account
  3. Click "Get API key" → "Create API key"
  4. Copy the key
  5. GitHub repo → Settings → Secrets → Actions → New secret:
       Name:  GEMINI_API_KEY
       Value: your key

FREE TIER LIMITS (more than enough):
  - 1,500 requests/day — we use 1/day
  - 15 requests/minute
  - No credit card required, never expires
  NOTE: on the free tier, Google may use prompts to improve their models.
  Since we're sending public outbreak data from a public webpage this
  is not a concern.

Run: python scrape.py
Requires: pip install requests cloudscraper google-genai
"""

import re
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ECDC_URL = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"
JSON_FILE = Path(__file__).parent / "data.json"

# ── Page fetch ─────────────────────────────────────────────────────────────────

def fetch_page():
    try:
        import cloudscraper
    except ImportError:
        print("FATAL: cloudscraper not installed. Run: pip install cloudscraper")
        sys.exit(1)
    print("Fetching ECDC page via cloudscraper...")
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
    """Strip HTML tags and normalize whitespace."""
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                        flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r'<[^>]+>', ' ', no_scripts)
    no_tags = no_tags.replace('\xa0', ' ').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', no_tags).strip()

def get_content_excerpt(clean_text, max_chars=3000):
    """
    Return only the relevant section of the page to minimize token usage.
    The outbreak numbers always appear after 'As of' or 'reported a total'.
    Sending the full 120KB page would waste tokens unnecessarily.
    """
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
- updated_date = the date the data refers to (not today).
- If a value is not in the text, use null.

PAGE TEXT:
"""
def extract_with_gemini(page_excerpt):
    """
    Use Gemini Flash (free tier) to extract case/death numbers.
    Returns dict with drc_cases, drc_deaths, uganda_cases, uganda_deaths, updated_date.
    """
    import os
    import time
    
    # Updated to perfectly match your GitHub Secrets configuration
    api_key = os.environ.get("GEMINI_API_KEY", "")    
    if not api_key:
        print("FATAL: GEMINI_API_KEY environment variable not set.")
        print("Get a free key at https://aistudio.google.com")
        print("Then add to GitHub Secrets as GEMINI_API_KEY.")
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        print("FATAL: google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    print("Extracting numbers via Gemini Flash (free tier)...")
    client = genai.Client(api_key=api_key)

    # Retry logic configuration for 429 rate limits
    max_retries = 3
    retry_delay = 15  # seconds in between attempts

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=EXTRACTION_PROMPT + page_excerpt,
            )
            response_text = response.text.strip()
            break  # Success! Break out of the retry loop
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"  Rate limited (429). Retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                continue
            else:
                print(f"FATAL: Gemini API call failed: {e}")
                sys.exit(1)

    print(f"  Gemini response: {response_text}")

    # Parse the JSON response
    try:
        # Strip markdown code fences if Gemini added them
        clean_response = re.sub(r'
http://googleusercontent.com/immersive_entry_chip/0

Commit this, push it up to GitHub, and give the action one more manual run. You should be completely in the clear!

    # Validate required fields
    for key in ["drc_cases", "drc_deaths", "uganda_cases", "uganda_deaths"]:
        if data.get(key) is None:
            print(f"FATAL: Gemini could not extract '{key}' from the page.")
            print("ECDC page may be Cloudflare-blocked (check page size vs 120KB expected).")
            sys.exit(1)

    return data

# ── Data persistence ───────────────────────────────────────────────────────────

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

# ── Main ───────────────────────────────────────────────────────────────────────

def scrape_ebola_data():
    print("========================================")
    print("Ebola Scraper — ECDC + Gemini extraction")
    print("========================================")

    html      = fetch_page()
    clean     = to_clean_text(html)
    excerpt   = get_content_excerpt(clean)
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
    print(f"DRC: {drc_cases} cases / {drc_deaths} deaths | "
          f"Uganda: {ug_cases} cases / {ug_deaths} deaths | "
          f"CFR: {cfr}% | as of {updated}")

    data = load_existing_data()
    data["updated"] = updated
    data["summary"]["confirmedDRC"]    = drc_cases
    data["summary"]["confirmedDeaths"] = drc_deaths
    data["summary"]["ugandaCases"]     = ug_cases
    data["summary"]["ugandaDeaths"]    = ug_deaths
    data["summary"]["cfrPercent"]      = cfr
    data["summary"]["dataSource"]      = "ECDC"
    data["timeline"] = update_timeline(
        data["timeline"], updated, total_cases, total_deaths
    )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Success: wrote to {JSON_FILE}")
    return data

if __name__ == "__main__":
    scrape_ebola_data()