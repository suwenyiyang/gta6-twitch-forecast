"""Forecast GTA VI's launch-day Twitch viewers hour by hour.

make.fun bounty: gta6-twitch-ccu-forecast-01
Entry program: `python3 -P -s main.py` (Python 3.13, standard library only,
no network). Reads every /input/<id>.in (or $MAKEFUN_INPUT_DIR) and writes
/output/<id>.out (or $MAKEFUN_OUTPUT_DIR) with, for each of hours 2..24 of
the release day (UTC), five quantiles (5%, 25%, 50%, 75%, 95%) of that
hour's concurrent-viewer count in the Twitch "Grand Theft Auto VI" category.

Input case (schema "makefun/twitch-ccu-case/v1", see the competition kit's
RULES.txt and starter/main.py):
    {"schema", "id", "game", "day_start", "checkpoint", "levels",
     "known": [{"hour": 1, "from", "until", "viewers", "channels", "reads"}],
     "forecast": [{"hour", "from", "until"} for hours 2..24],
     "history", "history_hourly", "streams", "categories_hourly",
     "comparables": [{"name", "day_start", "unlock", "platforms", "source",
                      "hourly": [{"from", "viewers", "channels"}]}], "notes"}

Output (exactly 23 rows, no other key):
    {"hours": [{"hour": h, "q": [q05, q25, q50, q75, q95]} for h in 2..24]}
each q a finite number > 0 and <= 1e9, non-decreasing within a row.

Model (see README.md for the full reasoning and the validation numbers)
----------------------------------------------------------------------
The case ships 38 past big launches in `comparables` (42 in the final),
each with hourly Twitch category viewers from 14 days before to 3 days
after its release day. The forecast is built from those comparables at
run time -- nothing about the curve is hard-coded from memory:

1. Anchor: p1 = the case's known hour-1 print (viewers in 00:00-01:00 UTC).
   Every forecast is a ratio to p1, so the program adapts to whatever
   hour 1 actually was, however big or small GTA VI's first hour is.

2. Median curve: for each comparable, take its release-day hourly
   viewers, divide by its own hour-1 print, and take logs. The median
   log-ratio over ALL usable comparables is the forecast's median curve.
   It has the shape a launch day actually has: a dip to ~0.75x in hours
   2-5 (deep night in Europe, US not yet unlocked), a slow climb as the
   US unlocks (05:00 UTC east coast, 08:00 UTC west coast), and a peak
   of ~2.0-2.4x in hours 19-20 (US afternoon/evening + EU evening prime
   time), easing to ~2.0x by hour 24. Using all comparables (n ~ 37)
   instead of only console-only ones (n = 7) makes the median far more
   stable; leave-one-out scoring on the console-only launches (the
   family GTA VI belongs to) prefers the all-launches median.

3. Spread: the honest uncertainty for a console-only rolling-midnight
   launch is much narrower than the spread across all launch types
   (PC-simultaneous launches have a totally different shape). So the
   log-spread s_h comes from the CONSOLE-ONLY comparables (platforms
   with no PC): the inter-quartile robust sigma of their log-ratios,
   plus the standard error of the median, plus a horizon term that
   widens the band as the forecast reaches further into the day:
       s_h = clamp(sqrt(sigma_c^2 + (sigma_c/sqrt(n_c))^2
                        + (0.08*sqrt(h-1))^2), 0.25, 1.0)
   Quantiles are lognormal around the median:
       q_tau(h) = p1 * exp(m_h + z_tau * s_h),
   z = [-1.645, -0.674, 0, 0.674, 1.645] for tau = 5/25/50/75/95%.

4. The target's own pre-release history (the "Grand Theft Auto VI"
   comparable, tier "target-history") is NOT a launch and is excluded,
   as is any comparable without a usable release day.

Scoring: pinball loss on the log scale. Leave-one-out validation on the
7 console-only comparables in the practice kit: this model 0.210 vs the
house persistence baseline 0.325 (same launches); 90% coverage 0.94.
The house baseline scores ~0.387 on the four practice cases. The final
is a different day, so no number here promises a final score.

Determinism and safety: no randomness, no clock, no network, no file
writes besides the outputs; inputs are read in name order. If anything
about a case is unusable, the program still writes a valid forecast
(the house persistence baseline) instead of crashing -- an invalid case
scores as if every quantile equalled hour 1's print and takes no prize,
so a crash is strictly worse than a plain forecast.
"""

import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves this file's directory OFF sys.path: put it back
# first, so a module shipped beside this file imports if ever added.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# House persistence baseline's spread (starter/main.py): the fallback
# forecast and the shape used when a case carries no usable comparables.
BASELINE_SIGMA = 0.15

# Spread parameters (validated by leave-one-out on the kit's comparables).
SPREAD_FLOOR = 0.25
SPREAD_CAP = 1.0
HORIZON_SIGMA = 0.08
MIN_HOUR1_VIEWERS = 100   # a comparable needs a real hour-1 print
MIN_RELEASE_HOURS = 20    # ... and a mostly complete release day


def round2(x: float) -> float | int:
    """x rounded to 2 decimals, half up (as JavaScript's Math.round)."""
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if value.is_integer() else value


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _is_target_history(comp: dict) -> bool:
    """True for the target game's own pre-release history, which is not a launch."""
    name = comp.get("name") or ""
    unlock = comp.get("unlock") or ""
    return (
        "Grand Theft Auto VI" in name
        or "Not a launch" in unlock
        or "target-history" in unlock
    )


def _is_console_only(comp: dict) -> bool:
    """Console-only launch (no PC): the family GTA VI belongs to."""
    platforms = comp.get("platforms") or ""
    return "PC" not in platforms and any(
        token in platforms for token in ("PS5", "PS4", "Xbox")
    )


def release_day_hours(comp: dict) -> dict[int, float]:
    """{hour 1..24: viewers} on the comparable's own release day (UTC)."""
    try:
        day_start = _parse_time(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return {}
    hours: dict[int, float] = {}
    for point in comp.get("hourly") or []:
        try:
            when = _parse_time(point["from"])
            viewers = point["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None:
            continue
        offset = (when - day_start).total_seconds() / 3600.0
        if 0 <= offset < 24:
            hours[int(offset) + 1] = max(1.0, float(viewers))
    return hours


def _median_log_ratio(ratios: list[float]) -> float | None:
    if len(ratios) < 3:
        return None
    return statistics.median(ratios)


def _robust_sigma(ratios: list[float]) -> tuple[float, int] | tuple[None, int]:
    """Inter-quartile robust sigma of log-ratios, and the sample size."""
    n = len(ratios)
    if n < 3:
        return None, n
    ordered = sorted(ratios)
    q1 = ordered[n // 4]
    q3 = ordered[(3 * n) // 4]
    return max(0.15, (q3 - q1) / 1.349), n


def build_curve(comparables: list[dict]) -> dict[int, tuple[float, float]]:
    """Return {hour: (median log-ratio to hour 1, log spread)} for h=2..24.

    Median from ALL usable comparables (stable, n ~ 37); spread from the
    console-only subset (honest conditional uncertainty for a console
    launch), plus the median's standard error and a horizon term.
    """
    usable: list[dict[int, float]] = []
    console: list[dict[int, float]] = []
    for comp in comparables or []:
        if _is_target_history(comp):
            continue
        hours = release_day_hours(comp)
        if 1 not in hours or hours[1] < MIN_HOUR1_VIEWERS:
            continue
        if len(hours) < MIN_RELEASE_HOURS:
            continue
        usable.append(hours)
        if _is_console_only(comp):
            console.append(hours)

    curve: dict[int, tuple[float, float]] = {}
    for hour in range(2, 25):
        all_ratios = [
            math.log(hours[hour] / hours[1]) for hours in usable if hour in hours
        ]
        median = _median_log_ratio(all_ratios)
        if median is None:
            median = 0.0
        sigma_c, n_c = _robust_sigma(
            [math.log(hours[hour] / hours[1]) for hours in console if hour in hours]
        )
        if sigma_c is None:
            # Too few console launches for this hour: fall back to the
            # all-launches spread, then to a plain 0.30.
            sigma_c, n_c = _robust_sigma(all_ratios)
            if sigma_c is None:
                sigma_c, n_c = 0.30, 1
        spread = math.sqrt(
            sigma_c * sigma_c
            + (sigma_c / math.sqrt(max(1, n_c))) ** 2
            + (HORIZON_SIGMA * math.sqrt(hour - 1)) ** 2
        )
        spread = min(max(spread, SPREAD_FLOOR), SPREAD_CAP)
        curve[hour] = (median, spread)
    return curve


def baseline_quantiles(p1: float, hour: int) -> list[float | int]:
    """House persistence baseline: flat at p1, spread 0.15*sqrt(h-1)."""
    spread = BASELINE_SIGMA * math.sqrt(hour - 1)
    return [round2(p1 * math.exp(z * spread)) for z in Z]


def hour_quantiles(p1: float, hour: int, curve: dict[int, tuple[float, float]]) -> list[float | int]:
    median, spread = curve.get(hour, (0.0, BASELINE_SIGMA * math.sqrt(hour - 1)))
    values = [p1 * math.exp(median + z * spread) for z in Z]
    # Clamp to the contest's (0, 1e9] and keep the row non-decreasing.
    out: list[float | int] = []
    previous = 0.0
    for value in values:
        value = min(max(value, 0.01), 1e9)
        value = max(value, previous)
        out.append(round2(value))
        previous = float(out[-1])
    return out


def hour1_print(case: dict) -> float:
    """Hour-1 viewers, with fallbacks that never crash the run."""
    for row in case.get("known") or []:
        if row.get("hour") == 1:
            try:
                return max(1.0, float(row["viewers"]))
            except (TypeError, ValueError):
                break
    # No usable hour-1 print: fall back to the last history point, else 1.
    history = case.get("history") or []
    if history:
        try:
            return max(1.0, float(history[-1]["viewers"]))
        except (TypeError, ValueError, KeyError):
            pass
    return 1.0


def forecast_case(case: dict) -> dict:
    p1 = hour1_print(case)
    try:
        curve = build_curve(case.get("comparables") or [])
    except Exception:
        curve = {}
    hours = []
    for row in case.get("forecast") or []:
        hour = row.get("hour")
        if not isinstance(hour, int) or not 2 <= hour <= 24:
            continue
        if curve:
            quantiles = hour_quantiles(p1, hour, curve)
        else:
            quantiles = baseline_quantiles(p1, hour)
        hours.append({"hour": hour, "q": quantiles})
    # Guarantee exactly hours 2..24, in order, whatever the case listed.
    seen = {row["hour"] for row in hours}
    for hour in range(2, 25):
        if hour not in seen:
            hours.append(
                {"hour": hour, "q": hour_quantiles(p1, hour, curve) if curve else baseline_quantiles(p1, hour)}
            )
    hours.sort(key=lambda row: row["hour"])
    return {"hours": hours}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            forecast = forecast_case(case)
        except Exception:
            # Never leave a case without an output: emit the baseline
            # shape for a 100,000-viewer hour 1 rather than crash.
            forecast = {
                "hours": [
                    {"hour": h, "q": baseline_quantiles(100000.0, h)}
                    for h in range(2, 25)
                ]
            }
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
