"""
analysis.py - Outbreak Trajectory Analysis

Computes three statistics from data.json's existing timeline and Google
Trends data, and writes the results into data.json under a new top-level
"trajectory_analysis" key. index.html reads that key to render the
"Outbreak Trajectory Analysis" dashboard section.

This is a Python port of an earlier R script (outbreak_analysis.R) that did
the same three analyses as a one-off. Porting to Python means this can run
as a third step in the daily GitHub Actions pipeline (alongside scrape.py
and trends.py) and auto-update every day, instead of being a standalone
script someone has to remember to re-run manually.

DELIBERATELY STDLIB-ONLY: no numpy/scipy/pandas. This is a GitHub Actions
runner with `pip install requests pytrends` already in update.yml - adding
heavier dependencies for three fairly simple calculations (a linear
regression slope, a CFR ratio, a rank correlation) isn't worth the extra
install time and another thing that can break in CI. Each calculation is
straightforward enough to write by hand.

SAME CAVEATS AS THE ORIGINAL R ANALYSIS APPLY (see outbreak_analysis.R for
the fuller discussion):
  - n=11 timeline points is enough for descriptive trend stats, not for
    rigorous inferential statistics. Treat confidence intervals here as
    indicative of curve-fit uncertainty, not formal epidemiological CIs.
  - CFR moving up or down does not by itself distinguish "ascertainment
    changing" from "lethality changing" - that needs case-level data this
    aggregate dashboard doesn't have.
  - The growth-rate model assumes one constant exponential rate across the
    whole window. The R diagnostics showed this outbreak's growth visibly
    bends through multiple phases - treat doubling time as a rough average,
    not the outbreak's current trajectory.
  - Google Trends scores are normalized to the peak value within the
    queried window, not absolute search volume - a single late spike can
    produce a misleadingly negative correlation with a steadily rising
    case count. Check the sign of the correlation against the actual
    search-interest chart before treating it as "interest declined."

Run standalone: python analysis.py
(reads + writes data.json in the same directory)
"""

import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data.json"


# ── Small stats helpers (stdlib only) ─────────────────────────────────────

def linreg(xs, ys):
    """
    Simple linear regression y = a + b*x via ordinary least squares.
    Returns (intercept, slope, r_squared, se_slope).
    se_slope is the standard error of the slope, used for a rough CI -
    NOT a substitute for a proper statistical package, but sufficient for
    the "rough indicative CI" framing used throughout this module.
    """
    n = len(xs)
    if n < 3:
        return None  # not enough points for a meaningful fit

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if ss_xx == 0:
        return None

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    # R-squared
    fitted = [intercept + slope * x for x in xs]
    ss_res = sum((y - f) ** 2 for y, f in zip(ys, fitted))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else None

    # Standard error of slope (for an approximate 95% CI via t-distribution
    # approximated as normal for small n - acceptable given we already flag
    # these CIs as rough/indicative throughout)
    if n > 2 and ss_xx > 0:
        residual_var = ss_res / (n - 2)
        se_slope = math.sqrt(residual_var / ss_xx)
    else:
        se_slope = None

    return {
        "intercept": intercept,
        "slope": slope,
        "r_squared": r_squared,
        "se_slope": se_slope,
        "n": n,
    }


def spearman_rho(xs, ys):
    """
    Spearman rank correlation, computed by hand (no scipy). Ties are
    handled with average ranks, same convention scipy/R use.
    Returns rho only - no p-value (computing a correct p-value with ties
    by hand isn't worth it here; the original R analysis already flagged
    these p-values as approximate/unreliable with small n and many ties,
    so we don't reproduce that potentially-misleading precision in Python).
    """
    n = len(xs)
    if n < 3:
        return None

    def rank(values):
        # average rank for ties
        indexed = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(indexed):
            j = i
            while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-indexed
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx = rank(xs)
    ry = rank(ys)
    return pearson_r(rx, ry)


def pearson_r(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


# ── Data loading ───────────────────────────────────────────────────────────

def load_data():
    if not DATA_FILE.exists():
        print("ERROR: data.json missing. Run scrape.py first.")
        return None
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: data.json is invalid JSON ({e}). Not running analysis "
              f"against a possibly-corrupt file - fix data.json first.")
        return None


def save_data(data):
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Analysis 1: CFR trend ─────────────────────────────────────────────────

def compute_cfr_trend(timeline):
    """Returns per-point CFR plus a simple direction-of-travel summary."""
    points = []
    for p in timeline:
        if p["cases"] > 0:
            cfr = round(100 * p["deaths"] / p["cases"], 2)
        else:
            cfr = None
        points.append({"date": p["date"], "cfr_percent": cfr})

    valid = [pt["cfr_percent"] for pt in points if pt["cfr_percent"] is not None]
    if len(valid) < 2:
        return {"points": points, "summary": None}

    # Compare the most recent few points to the few before that, rather than
    # just first-vs-last, since the first point or two can be extremely
    # noisy (small n).
    recent_window = valid[-3:] if len(valid) >= 3 else valid[-1:]
    earlier_window = valid[-6:-3] if len(valid) >= 6 else valid[:-len(recent_window)]
    recent_avg = sum(recent_window) / len(recent_window)
    earlier_avg = sum(earlier_window) / len(earlier_window) if earlier_window else None

    direction = None
    if earlier_avg is not None:
        diff = recent_avg - earlier_avg
        if abs(diff) < 1.0:
            direction = "stable"
        elif diff > 0:
            direction = "rising"
        else:
            direction = "falling"

    return {
        "points": points,
        "summary": {
            "current_cfr_percent": valid[-1],
            "min_cfr_percent": min(valid),
            "max_cfr_percent": max(valid),
            "recent_avg_percent": round(recent_avg, 2),
            "direction": direction,
        },
    }


# ── Analysis 2: exponential growth rate / doubling time ──────────────────

def compute_growth_rate(timeline):
    """
    Fits log(cumulative cases) ~ days_since_first_point via OLS.
    Returns growth rate (per day), doubling time, and a rough 95% CI band
    using slope +/- 1.96*SE as a normal approximation (flagged as
    approximate throughout - see module docstring).
    """
    points = [p for p in timeline if p["cases"] > 0]
    if len(points) < 3:
        return None

    dates = [datetime.strptime(p["date"], "%Y-%m-%d").date() for p in points]
    start = dates[0]
    xs = [(d - start).days for d in dates]
    ys = [math.log(p["cases"]) for p in points]

    fit = linreg(xs, ys)
    if fit is None:
        return None

    r = fit["slope"]
    doubling_time = (math.log(2) / r) if r > 0 else None

    ci = None
    if fit["se_slope"] is not None and r > 0:
        # NOTE: uses a normal approximation (slope +/- 1.96*SE) rather than a
        # t-distribution, unlike the original R version's confint(), which is
        # exact for OLS. With n=11 the difference is small (R gave [5.3, 11]
        # days for the same data; this gives something close but not
        # identical) - close enough for the "rough, indicative" framing this
        # module uses throughout, not worth pulling in scipy.stats for.
        r_lo = r - 1.96 * fit["se_slope"]
        r_hi = r + 1.96 * fit["se_slope"]
        # doubling time CI bounds flip order since r is in the denominator;
        # also guard against r_lo <= 0, which would make doubling time
        # undefined/infinite (the fit being too uncertain to say the
        # outbreak is even growing).
        dt_hi = (math.log(2) / r_lo) if r_lo > 0 else None
        dt_lo = (math.log(2) / r_hi) if r_hi > 0 else None
        ci = {"doubling_time_low": dt_lo, "doubling_time_high": dt_hi}

    return {
        "start_date": points[0]["date"],
        "end_date": points[-1]["date"],
        "growth_rate_per_day": round(r, 4),
        "doubling_time_days": round(doubling_time, 1) if doubling_time else None,
        "doubling_time_ci": (
            {k: round(v, 1) if v else None for k, v in ci.items()} if ci else None
        ),
        "r_squared": round(fit["r_squared"], 4) if fit["r_squared"] is not None else None,
        "n_points": fit["n"],
    }


# ── Analysis 3: search interest vs. case growth ───────────────────────────

def compute_search_correlation(timeline, search_timeline_ebola):
    """
    Spearman correlation between Google search interest ('Ebola' term) and
    cumulative case count, using last-observation-carried-forward to align
    the sparse case-report dates with the denser daily search series.
    """
    if not search_timeline_ebola or len(timeline) < 3:
        return None

    case_points = sorted(
        ((datetime.strptime(p["date"], "%Y-%m-%d").date(), p["cases"]) for p in timeline),
        key=lambda t: t[0],
    )
    search_points = sorted(
        ((datetime.strptime(s["time"], "%Y-%m-%d").date(), s["score"])
         for s in search_timeline_ebola),
        key=lambda t: t[0],
    )

    merged_cases, merged_scores = [], []
    for s_date, score in search_points:
        known_cases = [c for d, c in case_points if d <= s_date]
        if known_cases:
            merged_cases.append(known_cases[-1])  # most recent known cumulative count
            merged_scores.append(score)

    if len(merged_cases) < 10:
        return {"n": len(merged_cases), "rho": None,
                "note": "Not enough overlapping observations for a reliable estimate."}

    rho = spearman_rho(merged_scores, merged_cases)

    return {
        "n": len(merged_cases),
        "rho": round(rho, 3) if rho is not None else None,
        "interpretation_note": (
            "Google Trends scores are normalized to the peak within the queried "
            "window, not absolute volume. A negative rho here often reflects an "
            "early attention spike followed by decay, not declining concern - "
            "compare against the search interest chart before drawing conclusions."
        ),
    }


# ── Main entry point ──────────────────────────────────────────────────────

def run_analysis():
    data = load_data()
    if data is None:
        sys.exit(1)

    timeline = data.get("timeline", [])
    if len(timeline) < 3:
        print(f"Only {len(timeline)} timeline point(s) available - need at least 3 "
              f"for any of these analyses. Skipping (this is normal early on; "
              f"results will appear once scrape.py has accumulated more history).")
        data["trajectory_analysis"] = {
            "computed_at": date.today().strftime("%Y-%m-%d"),
            "cfr_trend": None,
            "growth_rate": None,
            "search_correlation": None,
            "insufficient_data": True,
        }
        save_data(data)
        return

    timeline_sorted = sorted(timeline, key=lambda p: p["date"])

    cfr_trend = compute_cfr_trend(timeline_sorted)
    growth_rate = compute_growth_rate(timeline_sorted)

    search_ebola = (
        data.get("google_trends_surveillance", {})
            .get("search_timeline", {})
            .get("Ebola", [])
    )
    search_correlation = compute_search_correlation(timeline_sorted, search_ebola)

    data["trajectory_analysis"] = {
        "computed_at": date.today().strftime("%Y-%m-%d"),
        "cfr_trend": cfr_trend,
        "growth_rate": growth_rate,
        "search_correlation": search_correlation,
        "insufficient_data": False,
    }

    save_data(data)

    print("Trajectory analysis complete:")
    if cfr_trend["summary"]:
        print(f"  CFR: {cfr_trend['summary']['current_cfr_percent']}% "
              f"(direction: {cfr_trend['summary']['direction']})")
    if growth_rate:
        print(f"  Doubling time: {growth_rate['doubling_time_days']} days "
              f"(r-squared: {growth_rate['r_squared']})")
    if search_correlation and search_correlation.get("rho") is not None:
        print(f"  Search/case correlation: rho={search_correlation['rho']} "
              f"(n={search_correlation['n']})")


if __name__ == "__main__":
    run_analysis()
