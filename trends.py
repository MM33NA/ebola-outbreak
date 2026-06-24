"""
trends.py - Google Trends Data Fetcher
Fetches live search interest trends for Ebola intent categories
and merges them into data.json without breaking CDC metrics.
Also processes rising queries into an aggregated word cloud schema.
Run: python trends.py

FIXES:
  1. Incremental saves after each section — 429 mid-run no longer wipes data
  2. word_cloud now saved inside google_trends_surveillance (correct key)
  3. Timeline skips dates already in data.json — fewer requests, fewer 429s
"""

import json
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from pytrends.request import TrendReq

DATA_FILE = Path(__file__).parent / "data.json"


# ── Helpers ────────────────────────────────────────────────────────────────────

def save_data(data):
    """Write data.json immediately — called after every successful section."""
    with DATA_FILE.open('w') as f:
        json.dump(data, f, indent=2)


def load_data():
    if not DATA_FILE.exists():
        print("ERROR: data.json missing. Run scrape.py first.")
        return None
    
    try:
        with DATA_FILE.open('r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("⚠️ WARNING: data.json is corrupted! Attempting automatic repair...")
        # Return an empty baseline structure so trends.py can still run and overwrite it
        return {
            "updated": "2026-06-23",
            "summary": {},
            "timeline": [],
            "events": [],
            "google_trends_surveillance": {"daily_history": {}}
        }

def get_active_period(updated_str):
    """Maps an ISO date string to the dashboard's active epidemiological period."""
    try:
        dt = datetime.strptime(updated_str, "%Y-%m-%d").date()
    except Exception:
        dt = date.today()

    if dt >= date(2026, 5, 24):
        return "2026-05-24"
    elif dt >= date(2026, 5, 15):
        return "2026-05-15"
    elif dt >= date(2026, 4, 20):
        return "2026-04-20"
    else:
        return "2026-03-30"

def process_word_cloud(surveillance, rising_searches):
    """
    Tokenizes raw query strings, filters stop words, and updates
    all-time and period-specific frequency matrices.
    
    FIX: now operates on the surveillance dict directly so word_cloud
    lands inside google_trends_surveillance — where the dashboard expects it.
    """
    raw_text = " ".join([item["query"] for item in rising_searches])
    words = re.findall(r'\b[a-zA-Z]{3,}\b', raw_text.lower())

    stop_words = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'ebola'}
    filtered = [w for w in words if w not in stop_words]
    daily_counts = Counter(filtered)

    # Ensure word_cloud structure exists inside surveillance
    if "word_cloud" not in surveillance:
        surveillance["word_cloud"] = {"all_time": {}, "periods": {}}

    wc = surveillance["word_cloud"]
    active_period = get_active_period(date.today().strftime("%Y-%m-%d"))

    if active_period not in wc["periods"]:
        wc["periods"][active_period] = {}

    for word, count in daily_counts.items():
        wc["all_time"][word] = wc["all_time"].get(word, 0) + count
        wc["periods"][active_period][word] = wc["periods"][active_period].get(word, 0) + count


def get_existing_dates(data):
    """
    Returns a set of date strings already in the timeline so we can
    skip re-fetching them — reduces requests and 429 risk.
    """
    try:
        existing = data.get("google_trends_surveillance", {}) \
                       .get("search_timeline", {}) \
                       .get("Ebola", [])
        return {entry["time"] for entry in existing}
    except Exception:
        return set()


# ── Main update function ───────────────────────────────────────────────────────

def update_trends_in_json():
    data = load_data()
    if data is None:
        return

    print("Connecting to Google Trends API...")
    pytrends = TrendReq(
        hl='en-US',
        tz=360,
        requests_args={
            'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        }
    )

    start_date     = "2026-04-17"
    today_date     = date.today().strftime('%Y-%m-%d')
    timeframe      = f"{start_date} {today_date}"
    existing_dates = get_existing_dates(data)

    # Ensure surveillance structure exists and preserves word_cloud across runs
    if "google_trends_surveillance" not in data:
        data["google_trends_surveillance"] = {
            "search_timeline": {"Ebola": [], "Symptoms": [], "Transmission": []},
            "rising_searches": [],
            "word_cloud": {"all_time": {}, "periods": {}}
        }

    surveillance = data["google_trends_surveillance"]

    # Preserve existing word_cloud if present — don't reset it on each run
    if "word_cloud" not in surveillance:
        surveillance["word_cloud"] = {"all_time": {}, "periods": {}}

    # ── SECTION 1: Historical timeline ────────────────────────────────────────
    keywords = ["Ebola", "Ebola symptoms", "Ebola transmission"]
    print(f"Fetching historical daily timelines for keywords: {keywords} ({timeframe})")

    try:
        pytrends.build_payload(keywords, cat=0, timeframe=timeframe)
        df = pytrends.interest_over_time()

        new_count = 0
        for index, row in df.iterrows():
            time_str = index.strftime('%Y-%m-%d')

            # FIX: skip dates we already have — avoids re-fetching entire history
            if time_str in existing_dates:
                continue

            surveillance["search_timeline"]["Ebola"].append(
                {"time": time_str, "score": int(row["Ebola"])})
            surveillance["search_timeline"]["Symptoms"].append(
                {"time": time_str, "score": int(row["Ebola symptoms"])})
            surveillance["search_timeline"]["Transmission"].append(
                {"time": time_str, "score": int(row["Ebola transmission"])})
            new_count += 1

        print(f"Timeline updated — {new_count} new date(s) added.")

        # FIX: save immediately after section 1 succeeds
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Timeline saved to data.json.")

    except Exception as e:
        print(f"Timeline fetch failed: {e}. Keeping existing timeline intact.")
        return

    # Cooldown before next request
    print("Pausing 15s before next request...")
    time.sleep(15)

    # ── SECTION 2: Rising searches ────────────────────────────────────────────
    print("Fetching anomalous rising search queries...")

    try:
        pytrends.build_payload(["Ebola"], cat=0, timeframe=timeframe)
        related_queries = pytrends.related_queries()

        rising_searches = []
        if "Ebola" in related_queries and related_queries["Ebola"]["rising"] is not None:
            df_rising = related_queries["Ebola"]["rising"].head(5)
            for _, row in df_rising.iterrows():
                rising_searches.append({
                    "query": row["query"],
                    "breakout_value": str(row["value"])
                })

        surveillance["rising_searches"] = rising_searches
        print(f"Rising searches updated — {len(rising_searches)} queries found.")

        # FIX: save immediately after section 2 succeeds
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Rising searches saved to data.json.")

    except Exception as e:
        print(f"Rising searches fetch failed: {e}. Keeping existing rising searches intact.")
        # Don't return — word cloud can still be built from existing rising_searches
        rising_searches = surveillance.get("rising_searches", [])

    # Cooldown before next request
    print("Pausing 10s before word cloud aggregation...")
    time.sleep(10)

    # ── SECTION 3: Word cloud ─────────────────────────────────────────────────
    print("Aggregating search queries into the cross-period word cloud...")

    try:
        process_word_cloud(surveillance, rising_searches)

        # FIX: word_cloud now lives inside google_trends_surveillance
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Word cloud saved to data.json inside google_trends_surveillance.")

    except Exception as e:
        print(f"Word cloud aggregation failed: {e}. Existing word cloud preserved.")

    print("\nAll sections complete. data.json is up to date.")


if __name__ == "__main__":
    update_trends_in_json()