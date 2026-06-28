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
from datetime import date, datetime, timedelta
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
    """
    Maps an ISO date string to a rolling weekly period bucket, e.g. '2026-06-22'
    for the Monday that starts the week containing that date.

    REPLACES the old hardcoded lookup table (2026-03-30/04-20/05-15/05-24),
    which had a finite list of boundaries and silently stopped producing new
    periods once the outbreak ran past the last one - every run for over a
    month was landing in the same '2026-05-24' bucket. Weekly buckets derived
    directly from the calendar never go stale.
    """
    try:
        dt = datetime.strptime(updated_str, "%Y-%m-%d").date()
    except Exception:
        dt = date.today()
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")

def process_word_cloud(surveillance, rising_searches, today_str, is_fresh_fetch):
    """
    Tokenizes raw query strings, filters stop words, and updates
    all-time and period-specific frequency matrices.

    FIX (double-counting): previously this ran on whatever `rising_searches`
    happened to be in memory, including the *stale* fallback used when
    Section 2's fetch failed (e.g. a 429). That meant a failed fetch caused
    the exact same 5 queries from days ago to get re-tokenized and added to
    the word cloud counts again, inflating frequencies for words that hadn't
    actually trended again. Now: if today's fetch wasn't fresh, skip word
    cloud aggregation entirely for this run rather than re-counting old data.
    """
    if not is_fresh_fetch:
        print("Skipping word cloud aggregation — today's rising-search fetch was not fresh "
              "(reused stale data), so nothing new to count.")
        return

    raw_text = " ".join([item["query"] for item in rising_searches])
    words = re.findall(r'\b[a-zA-Z]{3,}\b', raw_text.lower())

    stop_words = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'ebola'}
    filtered = [w for w in words if w not in stop_words]
    daily_counts = Counter(filtered)

    # Ensure word_cloud structure exists inside surveillance
    if "word_cloud" not in surveillance:
        surveillance["word_cloud"] = {"all_time": {}, "periods": {}}

    wc = surveillance["word_cloud"]
    active_period = get_active_period(today_str)

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


# Keep roughly 6 months of daily rising-search snapshots. At one entry per
# day this stays small (a few hundred KB at most), but caps growth so the
# file doesn't grow forever on an outbreak that runs for years.
RISING_SEARCH_HISTORY_RETENTION_DAYS = 180


def record_rising_searches_history(surveillance, today_str, rising_searches):
    """
    Stores today's rising-search snapshot under its own date key, instead of
    overwriting the single 'rising_searches' field every run. This is the
    fix for the actual bug being reported: previously each run replaced
    yesterday's top-5 queries with today's, so there was no way to see what
    people were searching for on any past date - only "right now."

    Schema:
      surveillance["rising_searches_history"] = {
        "2026-06-28": [{"query": ..., "breakout_value": ...}, ...],
        "2026-06-27": [...],
        ...
      }

    'rising_searches' (no _history suffix) is kept as-is for backward
    compatibility with index.html, and is always set to today's snapshot.
    """
    if "rising_searches_history" not in surveillance:
        surveillance["rising_searches_history"] = {}

    history = surveillance["rising_searches_history"]

    # Idempotent: re-running the same day overwrites only today's entry,
    # never duplicates or appends extra copies.
    history[today_str] = rising_searches

    # Prune anything older than the retention window.
    cutoff = date.today() - timedelta(days=RISING_SEARCH_HISTORY_RETENTION_DAYS)
    for old_date in list(history.keys()):
        try:
            if datetime.strptime(old_date, "%Y-%m-%d").date() < cutoff:
                del history[old_date]
        except ValueError:
            continue  # leave malformed keys alone rather than guessing

    surveillance["rising_searches"] = rising_searches
    return surveillance


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
    today_str = date.today().strftime("%Y-%m-%d")
    rising_searches_fetch_succeeded = False

    if "fetch_status" not in surveillance:
        surveillance["fetch_status"] = {}

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

        # FIX: was a flat overwrite (surveillance["rising_searches"] = rising_searches),
        # which silently discarded every previous day's snapshot. Now appends into
        # a dated history map so past days remain inspectable.
        surveillance = record_rising_searches_history(surveillance, today_str, rising_searches)
        surveillance["fetch_status"]["rising_searches"] = {
            "last_success": today_str,
            "last_attempt": today_str,
            "ok": True,
        }
        rising_searches_fetch_succeeded = True
        print(f"Rising searches updated — {len(rising_searches)} queries found.")

        # FIX: save immediately after section 2 succeeds
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Rising searches saved to data.json.")

    except Exception as e:
        print(f"Rising searches fetch failed: {e}. Keeping existing rising searches intact.")
        # FIX: record the failed attempt so staleness is visible instead of silent.
        # Previously a 429 here just printed a line to a log nobody reads; the
        # dashboard had no way to show "this hasn't updated in N days because
        # Google is rate-limiting us" versus "nothing new is trending."
        prev_status = surveillance["fetch_status"].get("rising_searches", {})
        surveillance["fetch_status"]["rising_searches"] = {
            "last_success": prev_status.get("last_success"),  # unchanged
            "last_attempt": today_str,
            "ok": False,
            "error": str(e)[:200],
        }
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        # Don't return — word cloud can still be built from existing rising_searches
        rising_searches = surveillance.get("rising_searches", [])

    # Cooldown before next request
    print("Pausing 10s before word cloud aggregation...")
    time.sleep(10)

    # ── SECTION 3: Word cloud ─────────────────────────────────────────────────
    print("Aggregating search queries into the cross-period word cloud...")

    try:
        process_word_cloud(surveillance, rising_searches, today_str, rising_searches_fetch_succeeded)

        # FIX: word_cloud now lives inside google_trends_surveillance
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Word cloud saved to data.json inside google_trends_surveillance.")

    except Exception as e:
        print(f"Word cloud aggregation failed: {e}. Existing word cloud preserved.")

    print("\nAll sections complete. data.json is up to date.")


if __name__ == "__main__":
    update_trends_in_json()