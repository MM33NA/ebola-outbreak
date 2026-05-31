import json
import os
from datetime import datetime
from pytrends.request import TrendReq

def get_google_trends_data():
    print("Connecting to Google Trends API...")
    # Initialize connection with a standard timeout and English language setting
    pytrends = TrendReq(hl='en-US', tz=360)
    
    # Define keywords you want to track
    keywords = ["Ebola", "Hantavirus"]
    
    # Build the payload for the last 7 days (global tracking)
    pytrends.build_payload(keywords, timeframe='now 7-d', geo='')
    
    # 1. Get Interest Over Time (The 0-100 search volume score)
    interest_df = pytrends.interest_over_time()
    
    trend_results = {}
    
    if not interest_df.empty:
        # Format the dataframe into clean timestamp strings and integers
        for keyword in keywords:
            trend_results[keyword] = [
                {
                    "time": index.strftime("%Y-%m-%d %H:%M"),
                    "score": int(row[keyword])
                }
                for index, row in interest_df.iterrows()
            ]
            
    # 2. Get Trending Related Queries
    related_queries = pytrends.related_queries()
    rising_queries = {}
    
    for keyword in keywords:
        rising_queries[keyword] = []
        if related_queries[keyword]['rising'] is not None:
            # Take the top 5 exploding search terms
            top_rising = related_queries[keyword]['rising'].head(5)
            for _, row in top_rising.iterrows():
                rising_queries[keyword].append({
                    "query": row['query'],
                    "breakout_value": str(row['value']) # Shows the % increase or 'Breakout'
                })

    return {
        "search_timeline": trend_results,
        "rising_searches": rising_queries
    }

if __name__ == "__main__":
    try:
        google_data = get_google_trends_data()
        
        # Load your existing index data file
        file_path = "data.json"
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    dashboard_data = json.load(f)
                except json.JSONDecodeError:
                    dashboard_data = {}
        else:
            dashboard_data = {}
            
        # Append your clean Google metrics node
        dashboard_data["google_trends_surveillance"] = google_data
        dashboard_data["last_trends_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        
        # Save it right back to your JSON file
        with open(file_path, "w") as f:
            json.dump(dashboard_data, f, indent=2)
            print("Successfully injected Google Trends tracking into data.json!")
            
    except Exception as e:
        print(f"Error fetching Google Trends data: {e}")