# Ebola Outbreak Dashboard — Data Pipeline

Live dashboard tracking the 2026 Bundibugyo Ebola outbreak (DRC/Uganda), with daily case/death scraping and Google Trends-based public intent surveillance.

**Live site:** https://mm33na.github.io/ebola-outbreak/

---

## How it works

A GitHub Actions workflow (`update.yml`) runs daily at 08:00 UTC:

1. `scrape.py` — fetches current case/death counts from ECDC, writes to `data.json`
2. `trends.py` — fetches Google Trends search-interest data and rising queries, merges into `data.json`
3. Commits and pushes the updated `data.json` back to the repo

`index.html` reads `data.json` client-side and renders everything — no backend server.

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
      "2026-06-28": [ { "query": "...", "breakout_value": "..." } ],
      "2026-06-27": [ ... ]
    },
    "fetch_status": {
      "rising_searches": { "last_success": "2026-06-28", "last_attempt": "2026-06-28", "ok": true }
    },
    "word_cloud": {
      "all_time": { "vaccine": 3, "symptoms": 2 },
      "periods": { "2026-06-22": { "vaccine": 1 } }
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

---

## Known issues & limitations

**Google Trends rate-limiting (pytrends 429s).** `related_queries()` (rising searches) is the call most often blocked — GitHub Actions runners share IP ranges with every other Action worldwide calling pytrends, so they get flagged faster than a residential IP. `interest_over_time()` (the search-volume timeline) is comparatively more reliable. Check `google_trends_surveillance.fetch_status.rising_searches` — if `ok: false` and `last_attempt` is recent but `last_success` is old, it's stuck on rate-limiting, not "nothing is trending."

**Timeline backfill is irregularly spaced, not truly daily.** ECDC/CDC don't expose a historical date-query endpoint — you can only ever scrape *today's* numbers. The pre-June-26 timeline points were manually backfilled from dated WHO DON reports, CDC situation pages, and MMWR, so the spacing between points varies (some are days apart, some over a week). Going forward, daily scraper runs will fill in the gaps naturally. The "Daily" chart toggle will show lumpy bars (one bar absorbing several real days) until that backfill smooths out — this is expected, not a bug.

**Google Trends history only starts from whenever this version was deployed.** `rising_searches_history` has no way to backfill the past — Google Trends doesn't expose historical "what was trending on date X." History accumulates only from this point forward.

**Word cloud period buckets are rolling weekly buckets** (Monday-start, computed from the date), not fixed epidemiological phases. They auto-advance forever and never go stale, but a "period" here just means "calendar week," not a meaningful outbreak phase.

---

## Workflow rules (for future edits)

- **Never edit Python files with PowerShell `Set-Content`** — always write a `.py` fix script and run it. (Encoding/line-ending issues from PowerShell have broken this before.)
- **`scrape.py` must hard-fail, never fabricate.** If ECDC's page structure changes and the regex stops matching, the correct behavior is `sys.exit(1)`, not falling back to placeholder numbers. A silent fallback is what caused the original bug where `data.json` was frozen with fake numbers for weeks.
- **`events` is never auto-generated.** If milestones look stale or missing, that's expected — go add them.
- **`git pull --rebase` before pushing** if working locally alongside the bot's daily commits, to avoid conflicts between your manual edits and the automated ones.

---

## Local testing

```bash
pip install requests pytrends --break-system-packages

python scrape.py   # hits live ECDC — will fail in network-restricted sandboxes
python trends.py   # hits live Google Trends — slow due to built-in cooldowns (~25s)
```

Both scripts read/write `data.json` in the same directory they're run from.