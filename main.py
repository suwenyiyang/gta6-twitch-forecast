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
   log-ratio is a WEIGHTED median, weighted by a Gaussian kernel on
   log launch size (bandwidth 1.3): a comparable whose first hour was
   as big as the target's counts most, one two orders of magnitude
   away nearly nothing. Size drives shape -- a 20k-viewer console
   launch climbs to several times its first hour by prime time, a
   900k launch opens near its peak and decays -- so this is what makes
   one program serve a small practice case and a GTA VI-sized final
   alike. The target's own pre-release history is excluded.

3. Spread: the honest uncertainty for a console-only rolling-midnight
   launch is much narrower than the spread across all launch types
   (PC-simultaneous launches have a totally different shape). So the
   log-spread s_h comes from the CONSOLE-ONLY comparables (platforms
   with no PC). Their inter-quartile robust sigma is measured across
   launches of every size, so it is shaded down by 0.85 now that the
   median is conditioned on size -- what is left is
       s_h = clamp(sqrt(sigma_c^2 + (sigma_c/sqrt(n_c))^2
                        + (0.08*sqrt(h-1))^2
                        + (0.10*excess)^2), 0.25, 1.0)
   with sigma_c the shaded console IQR and excess how far the target's
   first hour sits above every comparable: the further out on the size
   axis, the less the data can say.
   Quantiles are lognormal around the median:
       q_tau(h) = p1 * exp(m_h + z_tau * s_h),
   z = [-1.645, -0.674, 0, 0.674, 1.645] for tau = 5/25/50/75/95%.

4. Exclusions: the target's own pre-release history (the "Grand Theft
   Auto VI" comparable, tier "target-history") is not a launch and is
   excluded, as is any comparable without a usable release day (hour-1
   print under 100 viewers, or fewer than 20 of its 24 hours present).

Scoring: pinball loss on the log scale. Leave-one-out validation on the
7 console-only comparables in the practice kit, scoring the shipped
forecast_case() with each launch held out in turn: this model 0.175 vs
the house persistence baseline 0.325 on the same launches; 90% coverage
0.95. The house baseline scores ~0.387 on the four practice cases. The
final is a different day, so no number here promises a final score.

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
SIZE_BANDWIDTH = 1.3      # log-viewers bandwidth of the launch-size kernel
EXTRAPOLATION_SIGMA = 0.10  # extra spread per log unit the target exceeds every comparable
# The console IQR is measured across launches of every size; conditioning the median on launch
# size leaves less residual spread than that unconditional IQR, so shade it down.
CONSOLE_SIGMA_SCALE = 0.85
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


def _robust_sigma(ratios: list[float]) -> tuple[float, int] | tuple[None, int]:
    """Inter-quartile robust sigma of log-ratios, and the sample size."""
    n = len(ratios)
    if n < 3:
        return None, n
    ordered = sorted(ratios)
    q1 = ordered[n // 4]
    q3 = ordered[(3 * n) // 4]
    return max(0.15, (q3 - q1) / 1.349), n


def _kernel_weights(p1: float, sizes: list[float]) -> list[float]:
    """Gaussian kernel on log launch size: a comparable as big as the target counts most.

    Launch size drives the shape: a 20k-viewer console launch rises to several times its first
    hour by prime time, a 900k launch opens near its peak and decays. Weighting by how close a
    comparable's hour-1 print is to the target's, in log space, is what makes the median curve
    adapt to the target's scale. Shifted by the smallest distance first so no weight underflows.
    """
    if not sizes:
        return []
    target = math.log(max(1.0, p1))
    d2 = [(math.log(max(1.0, size)) - target) ** 2 for size in sizes]
    floor = min(d2)
    return [math.exp(-0.5 * (value - floor) / (SIZE_BANDWIDTH ** 2)) for value in d2]


def _weighted_median(values: list[float], weights: list[float]) -> float:
    """The value where the cumulative weight first passes half the total."""
    total = sum(weights)
    if total <= 0.0:
        return statistics.median(values)
    pairs = sorted(zip(values, weights))
    accumulated = 0.0
    for value, weight in pairs:
        accumulated += weight
        if accumulated >= total / 2.0:
            return value
    return pairs[-1][0]


def build_curve(comparables: list[dict], p1: float) -> dict[int, tuple[float, float]]:
    """Return {hour: (median log-ratio to hour 1, log spread)} for h=2..24.

    Median from ALL usable comparables, weighted towards launches of a similar size (so a
    GTA VI-sized first hour is forecast from GTA VI-sized launches); spread from the
    console-only subset (honest conditional uncertainty for a console launch), plus the
    median's standard error, a horizon term and an extrapolation term.
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

    if not usable:
        return {}

    sizes = [hours[1] for hours in usable]
    weights = _kernel_weights(p1, sizes)
    # How far, in log viewers, the target's first hour sits above every comparable it has:
    # the further out, the less the comparables can say, so the band has to widen.
    excess = max(0.0, math.log(max(1.0, p1)) - max(math.log(size) for size in sizes))

    curve: dict[int, tuple[float, float]] = {}
    for hour in range(2, 25):
        rows = [(hours, weight) for hours, weight in zip(usable, weights) if hour in hours]
        values = [math.log(hours[hour] / hours[1]) for hours, _ in rows]
        median = _weighted_median(values, [weight for _, weight in rows]) if values else 0.0
        sigma_c, n_c = _robust_sigma(
            [math.log(hours[hour] / hours[1]) for hours in console if hour in hours]
        )
        if sigma_c is None:
            # Too few console launches for this hour: fall back to the
            # all-launches spread, then to a plain 0.30.
            sigma_c, n_c = _robust_sigma(values)
            if sigma_c is None:
                sigma_c, n_c = 0.30, 1
        sigma_c *= CONSOLE_SIGMA_SCALE
        spread = math.sqrt(
            sigma_c * sigma_c
            + (sigma_c / math.sqrt(max(1, n_c))) ** 2
            + (HORIZON_SIGMA * math.sqrt(hour - 1)) ** 2
            + (EXTRAPOLATION_SIGMA * excess) ** 2
        )
        spread = min(max(spread, SPREAD_FLOOR), SPREAD_CAP)
        curve[hour] = (median, spread)
    return curve


def _clamp_row(values: list[float]) -> list[float | int]:
    """One output row: finite, above 0, at most 1e9, non-decreasing, rounded to 2 decimals."""
    out: list[float | int] = []
    previous = 0.0
    for value in values:
        if not math.isfinite(value):  # a NaN or an infinity from a pathological case
            value = 1e9 if value > 0 else 0.01
        value = min(max(value, 0.01), 1e9)
        value = max(value, previous)
        rounded = round2(value)
        out.append(rounded)
        previous = float(rounded)
    return out


def baseline_quantiles(p1: float, hour: int) -> list[float | int]:
    """House persistence baseline: flat at p1, spread 0.15*sqrt(h-1)."""
    spread = BASELINE_SIGMA * math.sqrt(hour - 1)
    return _clamp_row([p1 * math.exp(z * spread) for z in Z])


def hour_quantiles(p1: float, hour: int, curve: dict[int, tuple[float, float]]) -> list[float | int]:
    median, spread = curve.get(hour, (0.0, BASELINE_SIGMA * math.sqrt(hour - 1)))
    return _clamp_row([p1 * math.exp(median + z * spread) for z in Z])


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
        curve = build_curve(case.get("comparables") or [], p1)
    except Exception:
        curve = {}
    # Always exactly hours 2..24, each once, whatever the case's `forecast` list says: the
    # output shape is fixed by the rules, so a malformed list must not shrink or duplicate it.
    hours = [
        {"hour": hour, "q": hour_quantiles(p1, hour, curve) if curve else baseline_quantiles(p1, hour)}
        for hour in range(2, 25)
    ]
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
