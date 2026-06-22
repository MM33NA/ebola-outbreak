import re
import requests
import sys

def scrape_cdc_ebola():
    url = "https://www.cdc.gov/ebola/situation-summary/index.html"
    print("========================================")
    print("Ebola scraper — 2026-06-22")
    print("========================================")
    print(f"Fetching {url} ...")
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Error: Failed to fetch the page. {e}")
        sys.exit(1)
        
    html_content = response.text
    print(f"Page fetched — {len(html_content):,} chars")
    
    # Clean spacing to allow easy regex extraction across table layout elements
    clean_text = re.sub(r'\s+', ' ', html_content)
    
    # Isolate country segments exactly as features exist in the page text
    drc_segment = clean_text.split("DRC")[1].split("Uganda")[0] if "DRC" in clean_text else ""
    uganda_segment = clean_text.split("Uganda")[1] if "Uganda" in clean_text else ""

    # FIXED REGEX PATTERNS: Captures digits inside the new table formatting
    drc_cases = re.search(r"Confirmed cases\D*(\d+)", drc_segment, re.IGNORECASE)
    drc_deaths = re.search(r"Confirmed deaths\D*(\d+)", drc_segment, re.IGNORECASE)
    ug_cases = re.search(r"Confirmed cases\D*(\d+)", uganda_segment, re.IGNORECASE)
    ug_deaths = re.search(r"Confirmed deaths\D*(\d+)", uganda_segment, re.IGNORECASE)
    
    # Maps perfectly back into your original extracted dictionary layout
    extracted = {
        'suspected': 0, 
        'confirmed': int(drc_cases.group(1)) if drc_cases else 0,
        'suspected_deaths': 0, 
        'confirmed_deaths': int(drc_deaths.group(1)) if drc_deaths else 0,
        'uganda_cases': int(ug_cases.group(1)) if ug_cases else 0,
        'uganda_deaths': int(ug_deaths.group(1)) if ug_deaths else 0,
        'updated': '2026-06-22'
    }
    
    print(f"Extracted: {extracted}")
    
    # Your original exact exit validation feature logic
    if (extracted['confirmed'] + extracted['confirmed_deaths'] + extracted['uganda_cases'] + extracted['uganda_deaths']) == 0:
        print("WARNING: All zeros — CDC page structure may have changed.")
        print("Check scrape.py regex patterns.")
        print("Error: Process completed with exit code 1.")
        sys.exit(1)
        
    return extracted

if __name__ == "__main__":
    scrape_cdc_ebola()