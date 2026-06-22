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
    
    # 1. FIXED EXTRACTION: Target numbers relative to country names inside the layout directly
    # This completely bypasses the broken string splitting logic while preserving your keys.
    def extract_metric(country, metric_pattern, text):
        # Look for the country section block context in the source raw data
        match_block = re.search(rf"{country}.*?</tr>", text, re.DOTALL | re.IGNORECASE)
        if not match_block:
            # Fallback to general block lookup if specific row structures shift inline
            match_block = re.search(rf"{country}.*?(?=<tr|\<\/table)", text, re.DOTALL | re.IGNORECASE)
            
        block_text = match_block.group(0) if match_block else ""
        
        # Look for the digits near the confirmed keyword markers
        val_match = re.search(rf"{metric_pattern}\D*(\d+)", block_text, re.IGNORECASE)
        return int(val_match.group(1)) if val_match else 0

    # 2. Assign values accurately matching your original dictionary mapping blueprint
    extracted = {
        'suspected': 0, 
        'confirmed': extract_metric("DRC", "Confirmed cases", html_content),
        'suspected_deaths': 0, 
        'confirmed_deaths': extract_metric("DRC", "Confirmed deaths", html_content),
        'uganda_cases': extract_metric("Uganda", "Confirmed cases", html_content),
        'uganda_deaths': extract_metric("Uganda", "Confirmed deaths", html_content),
        'updated': '2026-06-22'
    }
    
    print(f"Extracted: {extracted}")
    
    # Your exact feature validation and threshold logic
    if (extracted['confirmed'] + extracted['confirmed_deaths'] + extracted['uganda_cases'] + extracted['uganda_deaths']) == 0:
        print("WARNING: All zeros — CDC page structure may have changed.")
        print("Check scrape.py regex patterns.")
        print("Error: Process completed with exit code 1.")
        sys.exit(1)
        
    return extracted

if __name__ == "__main__":
    scrape_cdc_ebola()