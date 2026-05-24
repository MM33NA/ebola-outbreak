# 2026 Ebola Outbreak Dashboard

Interactive dashboard tracking the 2026 Bundibugyo Ebola outbreak in the 
Democratic Republic of the Congo and Uganda, using data from the World Health 
Organization and Centers for Disease Control and Prevention.

🔗 **Live site:** https://mm33na.github.io/ebola-outbreak/

## What's included
- **Metrics** — suspected cases, deaths, confirmed cases, case fatality rate
- **Line chart** — outbreak progression over time
- **Transmission network** — confirmed case imports and contact movements
- **Health zones table** — affected areas in Ituri Province, DRC
- **Key events timeline** — from first case to PHEIC declaration

## Data sources
- [WHO Disease Outbreak News (DON602)](https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON602)
- [CDC Situation Summary](https://www.cdc.gov/ebola/situation-summary/index.html)
- [WHO PHEIC Declaration](https://www.who.int/news/item/17-05-2026-epidemic-of-ebola-disease-in-the-democratic-republic-of-the-congo-and-uganda-determined-a-public-health-emergency-of-international-concern)

## How data is updated
A Python scraper (`scrape.py`) fetches the latest figures from the CDC 
situation summary page daily via GitHub Actions and automatically updates 
`data.json`. The dashboard reads from `data.json` at load time.

To trigger a manual update:
**Actions tab → Daily data update → Run workflow**

## Built with
- HTML + CSS + JavaScript
- [Chart.js](https://www.chartjs.org/) — line chart
- GitHub Pages — hosting
- GitHub Actions — daily automated data updates

## Disclaimer
This is not a medical resource. For health guidance consult 
[WHO](https://www.who.int) and [CDC](https://www.cdc.gov) directly.

## Last updated
May 2026