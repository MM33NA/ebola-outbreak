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
    with DATA_FILE.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def load_data():
    if not DATA_FILE.exists():
        print("ERROR: data.json missing. Run scrape.py first.")
        return None
    with DATA_FILE.open('r', encoding='utf-8') as f:
        return json.load(f)

def clean_and_tokenize(rising_searches):
    """Cleans queries, removes stop words, and returns a localized count array."""
    raw_text = " ".join([item["query"] for item in rising_searches])
    # Match words with 3 or more letters
    words = re.findall(r'\b[a-zA-Z]{3,}\b', raw_text.lower())
    
    stop_words = {
        'the', 'and', 'for', 'with', 'from', 'this', 'that', 'ebola', 
        'news', 'cases', 'update', 'outbreak', 'virus', 'in', 'near', 'me'
    }
    filtered = [w for w in words if w not in stop_words]
    return Counter(filtered)

def get_existing_dates(data):
    try:
        existing = data.get("google_trends_surveillance", {}).get("search_timeline", {}).get("Ebola", [])
        return {entry["time"] for entry in existing}
    except Exception:
        return set()

# ── Main update function ───────────────────────────────────────────────────────

def update_trends_in_json():
    data = load_data()
    if data is None:
        return

    print("Connecting to Google Trends API via Pytrends...")
    pytrends = TrendReq(hl='en-US', tz=360, timeout=15)

    today_str = date.today().strftime('%Y-%m-%d')
    yesterday_str = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Target window is just the last 7 days to capture the absolute freshest trend index scale
    rolling_timeframe = "now 7-d" 

    # Ensure base structural nodes are explicitly defined
    if "google_trends_surveillance" not in data:
        data["google_trends_surveillance"] = {
            "search_timeline": {"Ebola": [], "Symptoms": [], "Transmission": []},
            "rising_searches": [],
            "word_cloud": {"all_time": {}, "daily_history": {}}
        }

    surveillance = data["google_trends_surveillance"]
    
    if "word_cloud" not in surveillance:
        surveillance["word_cloud"] = {"all_time": {}, "daily_history": {}}
    if "daily_history" not in surveillance["word_cloud"]:
        surveillance["word_cloud"]["daily_history"] = {}

    existing_dates = get_existing_dates(data)

    # ── SECTION 1: Append Timeline Metrics Safely ──────────────────────────
    keywords = ["Ebola", "Ebola symptoms", "Ebola transmission"]
    
    # If yesterday is already documented, skip network query to protect API rate caps
    if yesterday_str in existing_dates:
        print(f"Metrics for yesterday ({yesterday_str}) already cached. Skipping timeline call.")
    else:
        print(f"Fetching rolling timeline data...")
        try:
            pytrends.build_payload(keywords, cat=0, timeframe=rolling_timeframe)
            df = pytrends.interest_over_time()

            if not df.empty:
                # Group by day to collapse hourly spikes down into a solid baseline integer
                df_daily = df.resample('D').mean().astype(int)
                
                added_nodes = 0
                for index, row in df_daily.iterrows():
                    time_str = index.strftime('%Y-%m-%d')
                    if time_str not in existing_dates and time_str != today_str:
                        surveillance["search_timeline"]["Ebola"].append({"time": time_str, "score": int(row["Ebola"])})
                        surveillance["search_timeline"]["Symptoms"].append({"time": time_str, "score": int(row["Ebola symptoms"])})
                        surveillance["search_timeline"]["Transmission"].append({"time": time_str, "score": int(row["Ebola transmission"])})
                        added_nodes += 1
                
                print(f"✓ Appended {added_nodes} new historical day node(s).")
                data["google_trends_surveillance"] = surveillance
                save_data(data)
        except Exception as e:
            print(f"Warning: Timeline fetch encountered an error: {e}. Keeping current timeline state.")

        print("Pausing 10s to throttle api pacing...")
        time.sleep(10)

    # ── SECTION 2: Scrape Daily Rising Keywords for Word Cloud ────────────────
    print("Extracting fresh breakout query terms...")
    try:
        # Pull real-time trending spikes over the last 24 hours
        pytrends.build_payload(["Ebola"], cat=0, timeframe="now 1-d")
        related = pytrends.related_queries()
        
        rising_searches = []
        if "Ebola" in related and related["Ebola"]["rising"] is not None:
            df_rising = related["Ebola"]["rising"].head(8)
            for _, row in df_rising.iterrows():
                rising_searches.append({
                    "query": row["query"],
                    "breakout_value": str(row["value"])
                })
        
        surveillance["rising_searches"] = rising_searches
        print(f"✓ Harvested {len(rising_searches)} active trending phrases.")
    except Exception as e:
        print(f"Warning: Breakout query fetch failed: {e}. Working with cached queries.")
        rising_searches = surveillance.get("rising_searches", [])

    # ── SECTION 3: Process and Store Word Cloud Over Time ─────────────────────
    print("Processing localized text matrix frequencies...")
    try:
        daily_counts = clean_and_tokenize(rising_searches)
        wc = surveillance["word_cloud"]

        # Save frequencies to today's specific date bucket
        if today_str not in wc["daily_history"]:
            wc["daily_history"][today_str] = {}

        for word, count in daily_counts.items():
            # Update all-time totals
            wc["all_time"][word] = wc["all_time"].get(word, 0) + count
            # Update today's specific timeline node
            wc["daily_history"][today_str][word] = wc["daily_history"][today_str].get(word, 0) + count

        print(f"✓ Consolidated today's tracking data inside word_cloud['daily_history']['{today_str}']")
        data["google_trends_surveillance"] = surveillance
        save_data(data)
        
    except Exception as e:
        print(f"Error parsing word cloud structures: {e}")

    print("Execution finalized cleanly.")

if __name__ == "__main__":
    update_trends_in_json()