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
    
    # 1. Isolate the data table block from the HTML code to skip hidden layout text
    table_match = re.search(r"<table.*?>.*?</table>", html_content, re.DOTALL | re.IGNORECASE)
    table_text = table_match.group(0) if table_match else html_content

    # Helper function to parse metrics out of specific country blocks
    def extract_table_metric(country_keyword, metric_keyword, search_space):
        # Isolate text from the country name up to the next logical row boundary
        country_chunk = re.search(rf"{country_keyword}.*?(?=Totals|Uganda|\<\/table)", search_space, re.DOTALL | re.IGNORECASE)
        if not country_chunk:
            return 0
        segment = country_chunk.group(0)
        
        # Extract the digits right after the metric name
        metric_match = re.search(rf"{metric_keyword}\D*(\d+)", segment, re.IGNORECASE)
        return int(metric_match.group(1)) if metric_match else 0

    # 2. Map metrics into your exact feature structure
    extracted = {
        'suspected': 0, 
        'confirmed': extract_table_metric("DRC", "Confirmed cases", table_text),
        'suspected_deaths': 0, 
        'confirmed_deaths': extract_table_metric("DRC", "Confirmed deaths", table_text),
        'uganda_cases': extract_table_metric("Uganda", "Confirmed cases", table_text),
        'uganda_deaths': extract_table_metric("Uganda", "Confirmed deaths", table_text),
        'updated': '2026-06-22'
    }
    
    print(f"Extracted: {extracted}")
    
    # 3. Your original feature validation and error-exit logic
    total_metrics = (
        extracted['confirmed'] + 
        extracted['confirmed_deaths'] + 
        extracted['uganda_cases'] + 
        extracted['uganda_deaths']
    )
    
    if total_metrics == 0:
        print("WARNING: All zeros — CDC page structure may have changed.")
        print("Check scrape.py regex patterns.")
        print("Error: Process completed with exit code 1.")
        sys.exit(1)
        
    print("Scrape completed successfully.")
    return extracted

if __name__ == "__main__":
    scrape_cdc_ebola()