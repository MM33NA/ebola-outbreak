## Ebola Outbreak Dashboard — Data Pipeline

Live dashboard tracking the 2026 Bundibugyo Ebola outbreak (DRC/Uganda), with daily case/death scraping, Google Trends-based public intent surveillance, and automated trajectory analysis.

**Live site:** https://mm33na.github.io/ebola-outbreak/

---

## How it works

A GitHub Actions workflow (`update.yml`) runs daily at 08:00 UTC:

1. `scrape.py` — fetches current case/death counts from ECDC, writes to `data.json`
2. `trends.py` — fetches Google Trends search-interest data and rising queries, merges into `data.json`
3. `analysis.py` — recomputes CFR trend, growth rate/doubling time, and search-correlation statistics from the timeline `scrape.py` just updated
4. Commits and pushes the updated `data.json` back to the repo

`index.html` reads `data.json` client-side and renders everything — no backend server.

**If `scrape.py` hard-fails** (ECDC changed its page wording, network issue), the whole job stops before reaching `trends.py`/`analysis.py` — that day's data and analysis simply don't update, rather than being calculated against bad or missing input.

---

## Data sources

**Case/death counts: ECDC**, not CDC.

CDC's situation page stopped publishing exact numbers (now says things like "more than 1,000 cases" in prose) and can't be reliably parsed for a daily timeline. ECDC updates almost daily with exact, consistently-worded numbers:

> "the DRC Ministry of Health reported a total of 1 155 confirmed cases, including 304 confirmed related deaths"
> "Uganda had reported a total of 20 confirmed cases, including two deaths"

`scrape.py` regex-matches these two sentence patterns. If ECDC changes its wording and the regex stops matching, **the script hard-fails (exit 1) instead of writing fake/fallback numbers.** Check the Actions tab if a run shows a red X — that's the script telling you its parser needs updating, not a transient error to ignore.

**Search trends: Google Trends**, via the unofficial `pytrends` library. No official API exists, so this is inherently rate-limit-prone (see Known Issues).

---

## `data.json` structure

```json
{
  "updated": "2026-06-26",
  "summary": {
    "confirmedDRC": 1155,
    "confirmedDeaths": 304,
    "ugandaCases": 20,
    "ugandaDeaths": 2,
    "cfrPercent": 26.0
  },
  "timeline": [
    { "date": "2026-06-26", "cases": 1175, "deaths": 306 }
  ],
  "events": [
    { "date": "2026-05-17", "title": "WHO declares a Public Health Emergency of International Concern (PHEIC)" }
  ],
  "google_trends_surveillance": {
    "search_timeline": {
      "Ebola": [ { "time": "2026-06-28", "score": 8 } ],
      "Symptoms": [ ... ],
      "Transmission": [ ... ]
    },
    "rising_searches": [
      { "query": "ebola outbreak ituri", "breakout_value": "250" }
    ],
    "rising_searches_history": {
      "2026-06-28": [ { "query": "...", "breakout_value": "..." } ]
    },
    "fetch_status": {
      "rising_searches": { "last_success": "2026-06-28", "last_attempt": "2026-06-28", "ok": true }
    },
    "word_cloud": {
      "all_time": { "vaccine": 3, "symptoms": 2 },
      "periods": { "2026-06-22": { "vaccine": 1 } }
    }
  },
  "trajectory_analysis": {
    "computed_at": "2026-06-28",
    "insufficient_data": false,
    "cfr_trend": {
      "points": [ { "date": "2026-05-16", "cfr_percent": 50.0 }, ... ],
      "summary": {
        "current_cfr_percent": 26.04,
        "min_cfr_percent": 9.82,
        "max_cfr_percent": 50.0,
        "recent_avg_percent": 24.76,
        "direction": "rising"
      }
    },
    "growth_rate": {
      "start_date": "2026-05-16",
      "end_date": "2026-06-26",
      "growth_rate_per_day": 0.0966,
      "doubling_time_days": 7.2,
      "doubling_time_ci": { "doubling_time_low": 5.5, "doubling_time_high": 10.3 },
      "r_squared": 0.8238,
      "n_points": 11
    },
    "search_correlation": {
      "n": 44,
      "rho": -0.865,
      "interpretation_note": "..."
    }
  }
}
```

### Ownership — who writes what

| Field | Owner | Notes |
|---|---|---|
| `summary`, `timeline` | `scrape.py` | Overwritten/appended each run |
| `events` | **You, manually** | No script ever touches this. Add milestones yourself as the outbreak evolves. |
| `google_trends_surveillance.*` | `trends.py` | Appends/merges, never wipes |
| `trajectory_analysis` | `analysis.py` | **Fully recomputed from scratch every run** — not appended to, since it's a derived summary of the current timeline, not a historical record itself |

---

## Outbreak Trajectory Analysis — how it's calculated

This section (`analysis.py`, rendered as the "Outbreak Trajectory Analysis" cards on the dashboard, right after the case/death chart and milestones) runs three calculations against whatever is currently in `timeline`. It's pure-stdlib Python — no numpy/scipy/pandas — so it adds no new dependencies to the GitHub Actions pipeline. The math was originally prototyped in R (`outbreak_analysis.R` — not part of the auto-updating pipeline, kept as a reference/sandbox version) and the Python port was checked against it to confirm identical results.

### 1. Case Fatality Rate (CFR) trend

**Calculation:** `CFR = deaths / cases × 100` at each timeline point. Direction ("rising"/"falling"/"stable") compares the average of the most recent 3 points against the average of the 3 points before that (not just first-vs-last, since the earliest point or two can be extremely noisy with small case counts).

**How to interpret it:** CFR moving up or down does **not** by itself tell you whether the virus is becoming more or less lethal. Early in an outbreak, CFR is usually inflated because deaths (highly visible) get reported faster than mild or asymptomatic cases (easy to miss) — so an *early* declining CFR usually just means case-finding is catching up, not that the disease got milder. Conversely, a *later* rising CFR (which is what this outbreak currently shows — climbing from ~10% in late May back up to ~26% by late June) could mean genuinely worsening outcomes, but could equally mean reporting lag (deaths from already-known cases are still coming in, while brand-new mild cases haven't been counted yet). Distinguishing those explanations needs case-level (line-list) data, which this aggregate dashboard doesn't have — treat the direction as a flag worth investigating, not a conclusion.

### 2. Growth rate & doubling time

**Calculation:** fits `log(cumulative cases) = a + b × (days since first timeline point)` via ordinary least squares. The slope `b` is the daily growth rate; doubling time is `ln(2) / b`. A rough 95% confidence interval is computed from the slope's standard error using a normal approximation (not an exact t-distribution like proper statistical software would use — close enough for an indicative range, not precise enough to quote as a formal CI).

**How to interpret it:** This is a **single average rate fit across the entire observed window**, and the underlying data does not actually grow at one constant rate the whole time — plotting `log(cases)` against time visibly bends (shallower early, steeper in early June, flattening again later), meaning the outbreak has passed through multiple distinct growth phases. Report the doubling-time number as a rough average over the whole period, not as "how fast the outbreak is growing right now." The R² value tells you how well a single straight line fits the log-curve overall (0.82 here is moderately good but not excellent, consistent with that bend).

### 3. Search interest vs. case growth

**Calculation:** Spearman rank correlation between Google's daily "Ebola" search-interest score and the most recently known cumulative case count as of that date (case reports are sparser than daily search data, so the last known case count is carried forward to fill the gaps between report dates).

**How to interpret it:** Google Trends scores are normalized to the **peak value within whatever date range was queried** — they are not absolute search volume. This matters a lot here: this outbreak shows a sharp search-interest spike around the initial PHEIC announcement in mid-May, which then decays even as cases keep climbing through June. That produces a **negative** correlation (search interest trending down while cases trend up) — but it reflects *media-attention fatigue after the initial news cycle*, not declining public concern about the outbreak in any absolute sense. Always check the actual search-interest chart (just below this section) before drawing a conclusion from the correlation number alone — the sign can be misleading on its own.

---

## Known issues & limitations

**Trajectory analysis needs at least 3 timeline points.** Below that, `analysis.py` writes `insufficient_data: true` and the dashboard shows a "not enough data yet" message instead of any cards. This resolves itself automatically as `scrape.py` accumulates more daily points.

**Small sample size throughout.** With ~11 timeline points (and growing by ~1/day going forward), all three analyses are good for descriptive trend-spotting, not for rigorous inferential statistics. Don't quote the confidence intervals or correlation as if they came from a large, clean dataset.

**Google Trends rate-limiting (pytrends 429s).** `related_queries()` (rising searches) is the call most often blocked — GitHub Actions runners share IP ranges with every other Action worldwide calling pytrends, so they get flagged faster than a residential IP. `interest_over_time()` (the search-volume timeline, which feeds the correlation analysis) is comparatively more reliable. Check `google_trends_surveillance.fetch_status.rising_searches` — if `ok: false` and `last_attempt` is recent but `last_success` is old, it's stuck on rate-limiting, not "nothing is trending."

**Timeline backfill is irregularly spaced, not truly daily.** ECDC/CDC don't expose a historical date-query endpoint — you can only ever scrape *today's* numbers. The pre-June-26 timeline points were manually backfilled from dated WHO DON reports, CDC situation pages, and MMWR, so the spacing between points varies. This irregularity feeds directly into the growth-rate fit above — the model doesn't know some gaps are 1 day and others are a week, it just sees x/y pairs. Going forward, daily scraper runs will close those gaps naturally and make the trajectory analysis more reliable.

**Google Trends history only starts from whenever the history-tracking version was deployed.** `rising_searches_history` has no way to backfill the past — Google Trends doesn't expose historical "what was trending on date X." History accumulates only from that point forward.

**Word cloud period buckets are rolling weekly buckets** (Monday-start, computed from the date), not fixed epidemiological phases. They auto-advance forever and never go stale, but a "period" here just means "calendar week," not a meaningful outbreak phase.

---

## Workflow rules (for future edits)

- **Never edit Python files with PowerShell `Set-Content`** — always write a `.py` fix script and run it. (Encoding/line-ending issues from PowerShell have broken this before.)
- **`scrape.py` must hard-fail, never fabricate.** If ECDC's page structure changes and the regex stops matching, the correct behavior is `sys.exit(1)`, not falling back to placeholder numbers.
- **`events` is never auto-generated.** If milestones look stale or missing, that's expected — go add them.
- **`analysis.py` always recomputes from scratch** — there's no "update" logic to debug if numbers look wrong; it just re-derives everything from current `timeline` and `google_trends_surveillance.search_timeline.Ebola` every run. If the trajectory numbers look off, the timeline data feeding them is the place to check first.
- **`git pull --rebase` before pushing** if working locally alongside the bot's daily commits, to avoid conflicts between your manual edits and the automated ones.

---

## Local testing

```bash
pip install requests pytrends --break-system-packages

python scrape.py     # hits live ECDC — will fail in network-restricted sandboxes
python trends.py     # hits live Google Trends — slow due to built-in cooldowns (~25s)
python analysis.py   # stdlib only, instant — reads/writes data.json, no network needed
```

All three scripts read/write `data.json` in the same directory they're run from. `analysis.py` should be run after the other two if you want it to reflect the latest scrape.