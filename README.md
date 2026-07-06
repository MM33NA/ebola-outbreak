# Ebola Outbreak Dashboard — Data Pipeline

Live dashboard tracking the 2026 Bundibugyo Ebola outbreak (DRC/Uganda), with daily automated scraping, Google Trends public intent surveillance, and automated statistical trajectory analysis.

**Live site:** https://mm33na.github.io/ebola-outbreak/

---

## How it works

A GitHub Actions workflow (`update.yml`) runs daily at 08:00 UTC:

1. `scrape.py` — fetches the ECDC outbreak page, uses Gemini AI to extract current case/death counts, writes to `data.json`
2. `trends.py` — fetches Google Trends search-interest data, rising queries, and related topics; merges into `data.json`
3. `analysis.py` — recomputes CFR trend, exponential growth rate/doubling time, and search-interest correlation from the updated timeline
4. Commits and pushes the updated `data.json` back to the repo, which triggers GitHub Pages deployment

`index.html` reads `data.json` entirely client-side — no backend server.

---

## Setup (one-time)

### Gemini API key (free, no credit card)

1. Go to https://aistudio.google.com
2. Sign in with your Google account
3. Click **Get API key** → **Create API key** → copy the key
4. In your GitHub repo: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
   - Name: `GEMINI_API_KEY`
   - Value: your key

The free tier gives you 1,500 requests/day and 15 requests/minute. You use 1 per day.

### Local development

```bash
pip install requests cloudscraper pytrends google-genai

export GEMINI_API_KEY=your_key_here

python scrape.py     # fetch ECDC page + Gemini extraction
python trends.py     # Google Trends (slow, ~25s cooldowns built in)
python analysis.py   # statistical analysis — stdlib only, instant
```

---

## Why Gemini AI for extraction

ECDC changed their sentence wording 5+ times between May and July 2026. Each change broke the regex parser and required a manual fix and redeploy. Examples:

- `"DRC Ministry of Health reported a total of 1 155 confirmed cases, including 304 confirmed related deaths"`
- `"DRC) reported a total of 1 460 confirmed cases... A total 452 related deaths have been confirmed so far."`
- `"DRC) reported a total of 1 561 confirmed cases (based on data until 4 July), including 506 confirmed deaths"`

Gemini reads the page like a human and extracts the numbers regardless of wording. No regex, no maintenance required when ECDC updates their format.

**Free tier note:** Google may use free-tier prompts to improve their models. Since we send only public outbreak data from a public webpage, this is not a concern.

---

## Data sources

| Data | Source | Updated |
|---|---|---|
| Case/death counts | ECDC outbreak page | Almost daily |
| Search trends | Google Trends (via pytrends) | Daily |
| Statistical analysis | Computed from timeline | Daily |
| Milestones/events | Manually maintained | As needed |

**Why ECDC and not CDC:** CDC's situation page uses rounded prose ("more than 1,000 cases") — not parseable for a daily timeline. ECDC publishes exact confirmed case and death counts updated almost daily.

**Why cloudscraper:** ECDC sits behind Cloudflare, which blocks GitHub Actions' Azure datacenter IP ranges. cloudscraper handles the Cloudflare managed challenge that plain `requests` cannot bypass.

---

## `data.json` structure

```json
{
  "updated": "2026-07-05",
  "summary": {
    "confirmedDRC": 1561,
    "confirmedDeaths": 506,
    "ugandaCases": 20,
    "ugandaDeaths": 2,
    "cfrPercent": 32.1,
    "dataSource": "ECDC"
  },
  "timeline": [
    { "date": "2026-05-16", "cases": 10, "deaths": 5 },
    { "date": "2026-07-05", "cases": 1581, "deaths": 508 }
  ],
  "events": [
    { "date": "2026-05-17", "title": "WHO declares PHEIC" }
  ],
  "google_trends_surveillance": {
    "search_timeline": {
      "Ebola": [ { "time": "2026-07-05", "score": 8 } ],
      "Symptoms": [],
      "Transmission": []
    },
    "rising_searches": [
      { "query": "ebola outbreak ituri", "breakout_value": "250", "type": "rising" }
    ],
    "rising_searches_history": {
      "2026-07-05": [ { "query": "ebola outbreak ituri", "breakout_value": "250", "type": "rising" } ]
    },
    "related_topics": {
      "2026-07-05": [ { "title": "Ituri Province", "type": "rising", "value": "Breakout" } ]
    },
    "fetch_status": {
      "rising_searches": {
        "last_success": "2026-07-05",
        "last_attempt": "2026-07-05",
        "ok": true
      },
      "related_topics": {
        "last_success": "2026-07-05",
        "last_attempt": "2026-07-05",
        "ok": true
      }
    },
    "word_cloud": {
      "all_time": { "ituri": 5, "vaccine": 3, "bundibugyo": 2 },
      "periods": { "2026-06-30": { "ituri": 3 } }
    }
  },
  "trajectory_analysis": {
    "computed_at": "2026-07-05",
    "insufficient_data": false,
    "cfr_trend": {
      "points": [ { "date": "2026-05-16", "cfr_percent": 50.0 } ],
      "summary": {
        "current_cfr_percent": 32.1,
        "min_cfr_percent": 9.82,
        "max_cfr_percent": 50.0,
        "direction": "rising"
      }
    },
    "growth_rate": {
      "doubling_time_days": 7.2,
      "growth_rate_per_day": 0.0966,
      "r_squared": 0.8238,
      "n_points": 11
    },
    "search_correlation": {
      "rho": -0.865,
      "n": 44
    }
  }
}
```

### Data ownership — who writes what

| Field | Owner | Notes |
|---|---|---|
| `summary`, `timeline` | `scrape.py` | Overwritten/appended each run |
| `events` | **You, manually** | No script ever touches this |
| `google_trends_surveillance.*` | `trends.py` | Appends/merges, never wipes |
| `trajectory_analysis` | `analysis.py` | Fully recomputed from scratch every run |

---

## Outbreak Trajectory Analysis

`analysis.py` runs three calculations daily using only Python stdlib — no numpy, scipy, or pandas. No new dependencies beyond what the pipeline already installs.

### 1. Case Fatality Rate (CFR) trend

**How it is calculated:** `CFR = deaths / cases x 100` at each timeline point. Direction (rising/falling/stable) compares the average of the most recent 3 points against the 3 points before that — not just first-vs-last, since the earliest points are always noisy with small case counts.

**How to interpret it:** A declining CFR usually means case-finding is improving (mild cases getting counted that were previously missed), not that the disease became less lethal. The current rising CFR after the initial May drop is the more notable signal — it could indicate worsening outcomes, reporting lag, or a shift toward higher-risk populations. Distinguishing these explanations requires individual case-level data this aggregate dashboard does not have.

### 2. Exponential growth rate and doubling time

**How it is calculated:** Fits `log(cumulative cases) ~ days since first point` via ordinary least squares. Slope = daily growth rate. Doubling time = `ln(2) / slope`. Confidence interval uses a normal approximation of the slope standard error (not an exact t-distribution — close enough for the indicative framing used here).

**How to interpret it:** This is a single average rate across the whole observed window. The real outbreak has gone through multiple distinct growth phases, visible as a bend in the log-cases curve rather than a straight line. Report the doubling time as a rough average, not the current trajectory. The R-squared value indicates how well a single straight line fits — 0.82 is moderate, consistent with a multi-phase outbreak.

### 3. Search interest vs. case growth

**How it is calculated:** Spearman rank correlation between Google's daily Ebola search-interest score and the most recent known cumulative case count as of that date (last observation carried forward between sparse report dates).

**How to interpret it:** Google Trends scores are normalized to the peak within the queried date window, not absolute search volume. This outbreak shows a sharp spike around the PHEIC announcement in mid-May, then decay — even as cases kept climbing. That produces a negative correlation, reflecting media-attention fatigue after the initial news cycle, not declining public concern. Always read the search-interest chart alongside this number rather than treating the sign alone as meaningful.

---

## Google Trends word cloud

`trends.py` pulls three data sources per daily run to maximize word cloud vocabulary:

| Source | Volume | What it adds |
|---|---|---|
| Rising queries | Up to 25 | Recently accelerating search terms |
| Top queries | Up to 25 | Consistently high-volume search terms |
| Related topic titles | Up to 30 | Semantic entities (e.g. "Ituri Province", "Bundibugyo virus") |

Word cloud period buckets are rolling weekly (Monday-start), computed from the calendar date, so they never go stale regardless of how long the outbreak runs.

**Checking if trends are updating daily:** look at `google_trends_surveillance.fetch_status` in `data.json`. If `ok: false` and `last_attempt` is recent but `last_success` is several days old, Google is rate-limiting the `related_queries()` call. This is a known issue with pytrends on GitHub Actions shared IP ranges. The search-interest timeline (`search_timeline.Ebola`) is more reliable and rarely rate-limited.

---

## Milestone events

The `events` array in `data.json` is maintained manually. No script ever writes to it. Add new entries as the outbreak evolves, approximately when ECDC or WHO publishes a significant update. Format:

```json
{ "date": "YYYY-MM-DD", "title": "Description of the milestone" }
```

Current milestones span May 5 (WHO alert) through June 26 (ECDC 1,155 cases), sourced from WHO, CDC, ECDC, and MMWR. Wikipedia-only items were excluded as insufficiently corroborated.

---

## Timeline history and backfill

The pre-June-26 timeline points were manually backfilled from dated WHO Disease Outbreak News reports, CDC situation pages, and MMWR — not scraped. Spacing between points is irregular (some gaps are 1 day, some over a week). Going forward, daily scraper runs add one new point per day.

The Daily chart toggle will show uneven bars for irregular gaps. This is correct — it reflects the actual reporting cadence of public health updates, not a bug.

---

## Known issues

**Cloudflare blocks on ECDC:** ECDC's site blocks GitHub Actions Azure IP ranges. cloudscraper handles the managed JS challenge, but if it starts failing again the Actions log debug output shows the first 500 chars of what was extracted. Navigation menu text instead of outbreak data is the sign of a block.

**Google Trends 429 rate limiting:** pytrends `related_queries()` is the most commonly rate-limited call. If the word cloud looks frozen, check `fetch_status.rising_searches.last_success` in `data.json`. The search-interest timeline that feeds the trajectory analysis correlation is usually unaffected.

**Word cloud history:** `rising_searches_history` only accumulates from the date this version of `trends.py` was deployed forward. Google Trends has no API to backfill past days.

**Gemini free tier data use:** On the free tier, Google may use prompts to improve their models. Only public webpage content is sent, so this is not a concern for this project.

---

## Workflow rules

- **`events` is never auto-generated.** Add milestones manually as the outbreak evolves.
- **`analysis.py` always recomputes from scratch.** If trajectory numbers look wrong, check the `timeline` data feeding them first.
- **Never edit Python files with PowerShell `Set-Content`.** Encoding issues from PowerShell have caused `data.json` corruption before. Copy files manually or use git.
- **`git pull --rebase` before pushing** if working locally alongside the daily automated commits, to avoid merge conflicts.
- **If the scraper fails**, check the Actions log. The debug output shows the relevant page section so you can see exactly what Gemini received.

---

## File reference

| File | Purpose |
|---|---|
| `scrape.py` | ECDC page fetch via cloudscraper + Gemini AI extraction → `data.json` summary and timeline |
| `trends.py` | Google Trends fetch (search timeline, rising queries, related topics) → `data.json` surveillance section |
| `analysis.py` | CFR trend, growth rate, search correlation → `data.json` trajectory_analysis section |
| `update.yml` | GitHub Actions workflow — runs scrape, trends, analysis daily at 08:00 UTC |
| `index.html` | Dashboard — reads `data.json` client-side, renders all charts and sections |
| `data.json` | Single source of truth for all dashboard data |
| `chart_umd_min.js` | Bundled Chart.js library for the dashboard charts |
