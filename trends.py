"""
trends.py - Google Trends Data Fetcher
Fetches live search interest trends for Ebola intent categories 
and merges them into data.json without breaking CDC metrics.
Run: python trends.py
"""

import json
from pathlib import Path
from datetime import datetime
from pytrends.request import TrendReq

DATA_FILE = Path(__file__).parent / "data.json"

def fetch_live_trends():
    print("Connecting to Google Trends API...")
    pytrends = TrendReq(hl='en-US', tz=360)

    # 1. Build the payload for the 3 distinct Ebola public intent lines
    kw_list = ["Ebola", "Ebola symptoms", "Ebola transmission"]
    print(f"Fetching 7-day interest timelines for: {kw_list}")
    pytrends.build_payload(kw_list, cat=0, timeframe='now 7-d', geo='', gprop='')
    
    interest_df = pytrends.interest_over_time()
    
    search_timeline = {
        "Ebola": [],
        "Symptoms": [],
        "Transmission": []
    }
    
    # Process the live timeframe dataframe rows
    for index, row in interest_df.iterrows():
        time_str = index.strftime('%Y-%m-%d %H:%M:%S')
        search_timeline["Ebola"].append({"time": time_str, "score": int(row["Ebola"])})
        search_timeline["Symptoms"].append({"time": time_str, "score": int(row["Ebola symptoms"])})
        search_timeline["Transmission"].append({"time": time_str, "score": int(row["Ebola transmission"])})

    # 2. Grab anomalous rising spikes strictly for Ebola (Filters out unrelated noise)
    print("Fetching anomalous rising search queries...")
    pytrends.build_payload(["Ebola"], cat=0, timeframe='now 7-d')
    related_queries = pytrends.related_queries()
    
    rising_searches = []
    if "Ebola" in related_queries and related_queries["Ebola"]["rising"] is not None:
        df_rising = related_queries["Ebola"]["rising"].head(5)
        for _, row in df_rising.iterrows():
            rising_searches.append({
                "query": row["query"],
                "breakout_value": str(row["value"])
            })
            
    return search_timeline, rising_searches

def update_trends_in_json():
    if not DATA_FILE.exists():
        print("ERROR: data.json file missing. Run scrape.py first to initialize structure.")
        return

    # Read the existing data containing your CDC tracking info
    with DATA_FILE.open('r') as f:
        data = json.load(f)

    # Fetch fresh curves from Google Trends
    try:
        timeline, rising = fetch_live_trends()
    except Exception as e:
        print(f"Google API Error: {e}. Check internet connection or API rate-limits.")
        return

    # Inject/Overwrite only the trends object block
    data['google_trends_surveillance'] = {
        "search_timeline": timeline,
        "rising_searches": rising
    }

    # Save everything back safely
    with DATA_FILE.open('w') as f:
        json.dump(data, f, indent=2)
    print("Successfully merged live trends data into data.json!")

if __name__ == "__main__":
    update_trends_in_json()