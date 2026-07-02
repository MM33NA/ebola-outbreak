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
    except json.JSONDecodeError as e:
        # FIX: the old fallback structure was missing google_trends_surveillance's
        # inner keys (search_timeline, rising_searches, word_cloud), which caused
        # a KeyError crash in Section 1 the moment this fallback was used. This
        # is also the bug that triggered the user's actual error: data.json was
        # invalid JSON, the fallback below kicked in, the fallback was incomplete,
        # and the script crashed trying to use it.
        #
        # This does NOT explain *why* data.json became invalid JSON in the first
        # place - that's almost always one of: a run got killed mid-write
        # (Ctrl+C, terminal closed, laptop slept) leaving a half-written file, a
        # manual edit left a trailing comma / unclosed bracket, or a git merge
        # left conflict markers (<<<<<<<) in the file. Printing the exact parse
        # error and location below should make the actual cause visible instead
        # of papering over it with a guess.
        print(f"⚠️ WARNING: data.json is corrupted ({e}).")
        print(f"   Error at line {e.lineno}, column {e.colno} (character {e.pos}).")
        print("   This usually means a previous run was interrupted mid-write,")
        print("   a manual edit left invalid syntax, or a git merge left conflict")
        print("   markers in the file. Open data.json and check around that line.")
        print("   Building a clean baseline structure so this run can still proceed —")
        print("   but summary/timeline/events from before the corruption will be lost")
        print("   unless you recover them from git history.")
        return {
            "updated": None,
            "summary": {},
            "timeline": [],
            "events": [],
            "google_trends_surveillance": {
                "search_timeline": {"Ebola": [], "Symptoms": [], "Transmission": []},
                "rising_searches": [],
                "rising_searches_history": {},
                "fetch_status": {},
                "word_cloud": {"all_time": {}, "periods": {}},
            },
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

def process_word_cloud(surveillance, rising_searches, topic_entries, today_str, is_fresh_fetch):
    """
    Tokenizes raw query strings AND related topic titles, filters stop words,
    and updates all-time and period-specific frequency matrices.

    EXPANDED: previously only tokenized rising_searches (up to 5 queries),
    giving ~8-12 usable words. Now also tokenizes related_topics titles
    (e.g. "Democratic Republic of the Congo", "Public health emergency")
    which add richer, more meaningful vocabulary. Stop words list expanded
    substantially to filter generic noise that's plentiful when you have
    50+ source strings instead of 5.

    FIX (double-counting): if today's fetch wasn't fresh, skip aggregation
    entirely rather than re-counting stale data from a previous run.
    """
    if not is_fresh_fetch:
        print("Skipping word cloud aggregation — today's fetch was not fresh "
              "(reused stale data), so nothing new to count.")
        return

    # Combine query strings and topic titles into one text corpus
    query_text = " ".join([item["query"] for item in rising_searches])
    topic_text = " ".join([item["title"] for item in topic_entries if item.get("title")])
    raw_text = f"{query_text} {topic_text}"

    words = re.findall(r'\b[a-zA-Z]{3,}\b', raw_text.lower())

    # Expanded stop words: generic terms that appear constantly in health/news
    # queries regardless of the specific outbreak, plus pytrends artifacts.
    stop_words = {
        # English function words
        'the', 'and', 'for', 'with', 'from', 'this', 'that', 'are', 'was',
        'has', 'have', 'had', 'not', 'but', 'they', 'his', 'her', 'its',
        'been', 'who', 'what', 'when', 'how', 'all', 'one', 'can', 'more',
        'will', 'than', 'also', 'into', 'out', 'about', 'their', 'which',
        # Too generic in a disease-outbreak context to be informative
        'ebola', 'virus', 'disease', 'outbreak', 'case', 'cases', 'death',
        'deaths', 'news', 'update', 'latest', 'new', 'today', 'week',
        'health', 'public', 'report', 'reported', 'africa',
        # Google taxonomy noise
        'topic', 'search', 'query', 'trending', 'related',
    }
    filtered = [w for w in words if w not in stop_words]
    daily_counts = Counter(filtered)

    if not daily_counts:
        print("Word cloud: no new words after filtering — nothing to add.")
        return

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

    print(f"Word cloud: added {len(daily_counts)} distinct words "
          f"({sum(daily_counts.values())} total tokens) to period {active_period}.")


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

    # FIX: this used to be `if "google_trends_surveillance" not in data:` — an
    # all-or-nothing check. That let a *partially* formed surveillance dict
    # (e.g. the old corrupted-file fallback, which had the key present but
    # missing search_timeline/rising_searches inside it) slip through
    # untouched, causing a KeyError crash later. Defaulting each inner key
    # individually means it's now structurally impossible for this object to
    # be missing a field this script depends on, regardless of where `data`
    # came from (a fresh file, scrape.py's output, or a corruption fallback).
    if "google_trends_surveillance" not in data or not isinstance(data["google_trends_surveillance"], dict):
        data["google_trends_surveillance"] = {}

    surveillance = data["google_trends_surveillance"]
    surveillance.setdefault("search_timeline", {})
    surveillance["search_timeline"].setdefault("Ebola", [])
    surveillance["search_timeline"].setdefault("Symptoms", [])
    surveillance["search_timeline"].setdefault("Transmission", [])
    surveillance.setdefault("rising_searches", [])
    surveillance.setdefault("rising_searches_history", {})
    surveillance.setdefault("fetch_status", {})
    surveillance.setdefault("word_cloud", {"all_time": {}, "periods": {}})

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

    # ── SECTION 2: Rising + top queries ──────────────────────────────────────
    # EXPANDED: previously only pulled top 5 rising queries, which gave ~8-12
    # words to tokenize after stop-word filtering — far too thin for a useful
    # word cloud. Now pulls up to 25 rising AND up to 25 top queries from the
    # same related_queries() call (one API request, two dataframes in the
    # response). "Rising" = recently accelerating searches; "top" = consistently
    # high-volume searches over the period. Together they give a fuller picture
    # of both sustained interest and emerging topics.
    print("Fetching related queries (rising + top, up to 25 each)...")
    today_str = date.today().strftime("%Y-%m-%d")
    rising_searches_fetch_succeeded = False

    if "fetch_status" not in surveillance:
        surveillance["fetch_status"] = {}

    try:
        pytrends.build_payload(["Ebola"], cat=0, timeframe=timeframe)
        related_queries = pytrends.related_queries()

        rising_searches = []
        if "Ebola" in related_queries:
            # Rising queries — up from head(5) to head(25)
            if related_queries["Ebola"]["rising"] is not None:
                df_rising = related_queries["Ebola"]["rising"].head(25)
                for _, row in df_rising.iterrows():
                    rising_searches.append({
                        "query": row["query"],
                        "breakout_value": str(row["value"]),
                        "type": "rising"
                    })

            # Top queries — new addition, same response object, no extra API call
            if related_queries["Ebola"]["top"] is not None:
                df_top = related_queries["Ebola"]["top"].head(25)
                for _, row in df_top.iterrows():
                    rising_searches.append({
                        "query": row["query"],
                        "breakout_value": str(row["value"]),
                        "type": "top"
                    })

        surveillance = record_rising_searches_history(surveillance, today_str, rising_searches)
        surveillance["fetch_status"]["rising_searches"] = {
            "last_success": today_str,
            "last_attempt": today_str,
            "ok": True,
            "rising_count": sum(1 for q in rising_searches if q.get("type") == "rising"),
            "top_count": sum(1 for q in rising_searches if q.get("type") == "top"),
        }
        rising_searches_fetch_succeeded = True
        rising_count = sum(1 for q in rising_searches if q.get("type") == "rising")
        top_count = sum(1 for q in rising_searches if q.get("type") == "top")
        print(f"Queries updated — {rising_count} rising + {top_count} top = {len(rising_searches)} total.")

        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Rising + top queries saved to data.json.")

    except Exception as e:
        print(f"Related queries fetch failed: {e}. Keeping existing queries intact.")
        prev_status = surveillance["fetch_status"].get("rising_searches", {})
        surveillance["fetch_status"]["rising_searches"] = {
            "last_success": prev_status.get("last_success"),
            "last_attempt": today_str,
            "ok": False,
            "error": str(e)[:200],
        }
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        rising_searches = surveillance.get("rising_searches", [])

    # Cooldown before topics request
    print("Pausing 15s before related topics fetch...")
    time.sleep(15)

    # ── SECTION 2b: Related topics ────────────────────────────────────────────
    # related_topics() returns broader semantic entities (e.g. "Democratic
    # Republic of the Congo", "Public health emergency", "Bundibugyo virus")
    # rather than raw query strings. These enrich the word cloud with
    # vocabulary that users wouldn't type verbatim but that represents the
    # actual subject matter people are reading about. Saved separately so
    # the dashboard can distinguish "what people searched" from "what topics
    # those searches are about."
    print("Fetching related topics...")
    topics_fetch_succeeded = False

    try:
        pytrends.build_payload(["Ebola"], cat=0, timeframe=timeframe)
        related_topics_result = pytrends.related_topics()

        topic_entries = []
        if "Ebola" in related_topics_result:
            for topic_type in ["rising", "top"]:
                df_t = related_topics_result["Ebola"].get(topic_type)
                if df_t is not None and not df_t.empty:
                    for _, row in df_t.head(15).iterrows():
                        # topic_title is the human-readable name;
                        # topic_type is the Google taxonomy type (e.g. "Health")
                        title = str(row.get("topic_title", "")).strip()
                        if title:
                            topic_entries.append({
                                "title": title,
                                "type": topic_type,
                                "value": str(row.get("value", "")),
                            })

        surveillance.setdefault("related_topics", {})
        surveillance["related_topics"][today_str] = topic_entries
        surveillance["fetch_status"]["related_topics"] = {
            "last_success": today_str,
            "last_attempt": today_str,
            "ok": True,
            "count": len(topic_entries),
        }
        topics_fetch_succeeded = True
        print(f"Related topics updated — {len(topic_entries)} entries.")

        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Related topics saved to data.json.")

    except Exception as e:
        print(f"Related topics fetch failed: {e}. Keeping existing topics intact.")
        prev_status = surveillance["fetch_status"].get("related_topics", {})
        surveillance["fetch_status"]["related_topics"] = {
            "last_success": prev_status.get("last_success"),
            "last_attempt": today_str,
            "ok": False,
            "error": str(e)[:200],
        }
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        topic_entries = []

    # ── SECTION 3: Word cloud ─────────────────────────────────────────────────
    # Now fed by both queries (rising + top, up to 50 strings) AND topic
    # titles (up to 30 entities) — substantially richer than the original
    # 5-query-only source.
    print("Pausing 10s before word cloud aggregation...")
    time.sleep(10)
    print("Aggregating queries + topics into the cross-period word cloud...")

    try:
        process_word_cloud(
            surveillance, rising_searches, topic_entries,
            today_str, rising_searches_fetch_succeeded
        )

        # FIX: word_cloud now lives inside google_trends_surveillance
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        print("✓ Word cloud saved to data.json inside google_trends_surveillance.")

    except Exception as e:
        print(f"Word cloud aggregation failed: {e}. Existing word cloud preserved.")

    print("\nAll sections complete. data.json is up to date.")

if __name__ == "__main__":
    update_trends_in_json()