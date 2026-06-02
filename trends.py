"""
trends.py - Google Trends Data Fetcher
Fetches live search interest trends for Ebola intent categories 
and merges them into data.json without breaking CDC metrics.
Run: python trends.py
"""

import json
from pathlib import Path
from datetime import datetime, date
from pytrends.request import TrendReq

DATA_FILE = Path(__file__).parent / "data.json"

def fetch_live_trends():
    print("Connecting to Google Trends API...")
    pytrends = TrendReq(hl='en-US', tz=360)

    # Hardcoded to start exactly 1 week prior to the sentinel case alert
    start_date = "2026-04-17"
    today_date = date.today().strftime('%Y-%m-%d')
    historical_timeframe = f"{start_date} {today_date}"
    
    search_timeline = {
        "Ebola": [],
        "Symptoms": [],
        "Transmission": []
    }
    
    # FETCH KEYWORD 1 (Ebola)
    print(f"Fetching historical daily timeline for: Ebola ({historical_timeframe})")
    pytrends.build_payload(["Ebola"], cat=0, timeframe=historical_timeframe)
    df1 = pytrends.interest_over_time()
    for index, row in df1.iterrows():
        search_timeline["Ebola"].append({"time": index.strftime('%Y-%m-%d'), "score": int(row["Ebola"])})

    # FETCH KEYWORD 2 (Ebola symptoms)
    print(f"Fetching historical daily timeline for: Ebola symptoms ({historical_timeframe})")
    pytrends.build_payload(["Ebola symptoms"], cat=0, timeframe=historical_timeframe)
    df2 = pytrends.interest_over_time()
    for index, row in df2.iterrows():
        search_timeline["Symptoms"].append({"time": index.strftime('%Y-%m-%d'), "score": int(row["Ebola symptoms"])})

    # FETCH KEYWORD 3 (Ebola transmission)
    print(f"Fetching historical daily timeline for: Ebola transmission ({historical_timeframe})")
    pytrends.build_payload(["Ebola transmission"], cat=0, timeframe=historical_timeframe)
    df3 = pytrends.interest_over_time()
    for index, row in df3.iterrows():
        search_timeline["Transmission"].append({"time": index.strftime('%Y-%m-%d'), "score": int(row["Ebola transmission"])})

    # 2. Grab anomalous rising spikes strictly for Ebola
    print("Fetching anomalous rising search queries...")
    pytrends.build_payload(["Ebola"], cat=0, timeframe=historical_timeframe)
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

    with DATA_FILE.open('r') as f:
        data = json.load(f)

    try:
        timeline, rising = fetch_live_trends()
    except Exception as e:
        print(f"Google API Error: {e}. Check internet connection or API rate-limits.")
        return

    # Injects the structured keys the frontend is waiting for
    data['google_trends_surveillance'] = {
        "search_timeline": timeline,
        "rising_searches": rising
    }

    with DATA_FILE.open('w') as f:
        json.dump(data, f, indent=2)
    print("Successfully merged daily historical trends data into data.json!")

if __name__ == "__main__":
    update_trends_in_json()