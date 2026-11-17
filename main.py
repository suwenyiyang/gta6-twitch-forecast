"""Hour-by-hour forecast of Grand Theft Auto VI's launch-day Twitch audience.

make.fun bounty gta6-twitch-ccu-forecast-01.

The program is given one case: the category's concurrent-viewer count for the first hour of the
game's release day (00:00-01:00 UTC), plus the hourly curves of past big launches. It writes, for
each of hours 2 to 24, five quantiles of that hour's count. Scoring is the mean pinball loss on
the log scale, so the job is five honest numbers an hour, not one guess.

Forecasting idea
----------------
Every forecast is a multiple of the first hour's count, so the same program serves a 20k-viewer
practice case and a seven-figure launch. The multiples come from the comparables the case ships
(38 in the practice, 42 in the final), and they are not pooled blindly: how a launch day decays
depends mostly on how big the launch was. A 20k-viewer console exclusive keeps climbing for
twelve hours and peaks at several times its first hour; a 900k premiere is already near its peak
in hour one and eases off. So each comparable is weighted by a Gaussian kernel on the log of its
own first hour, close in size counting most (bandwidth 1.3 in log viewers), and the weighted
median of the log-multiples is the central forecast.

Uncertainty is read off the console-only comparables -- the family GTA VI is in, console games
that unlocked at midnight local time, whose days look nothing like a PC-simultaneous premiere.
Their inter-quartile spread is shaded down a little, because it is measured across launches of
every size while the median is now conditional on size, and then widened by three more terms:
growing uncertainty with the horizon, the standard error of the median itself, and how far the
target's first hour sits beyond every comparable on the size axis (extrapolation). Five
lognormal quantiles around the median at z = -1.645, -0.674, 0, 0.674, 1.645 finish the row.

Input and output
----------------
Reads every <id>.in in $MAKEFUN_INPUT_DIR (default /input); writes <id>.out to
$MAKEFUN_OUTPUT_DIR (default /output). Output is one JSON object, one key "hours", 23 rows of
{"hour": h, "q": [q05, q25, q50, q75, q95]} for h = 2..24, each q finite, above 0, at most 1e9
and non-decreasing. Standard library only; no network, no clock, no randomness, so two runs on
the same case write the same bytes.

The program never raises out of a case. A case it cannot model falls back to the house
persistence shape (flat at the first hour, spread 0.15 per root hour), because a crash or a
malformed file invalidates the case, while a plain forecast still scores.
"""

import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Put this file's own directory on sys.path: the grader runs `python3 -P -s main.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The five levels the contest scores, in the order each row lists them, and the standard normal's
# quantile at each level.
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.645, -0.674, 0.0, 0.674, 1.645)

# House persistence spread, used by the fallback and as the last-resort spread.
BASE_SIGMA = 0.15
# Width of the size kernel, in natural-log viewers.
SIZE_BANDWIDTH = 1.3
# The console IQR is unconditional on size; the median is not, so it is shaded by this.
CONSOLE_SHRINK = 0.85
# Extra spread per root hour of horizon, and per log viewer the target exceeds every comparable.
HORIZON_RATE = 0.08
EXTRAPOLATION_RATE = 0.10
# Bounds on the log spread, and the floor on a measured inter-quartile sigma.
SIGMA_FLOOR = 0.25
SIGMA_CEILING = 1.0
IQR_FLOOR = 0.15
# A comparable is usable only with a real first hour and a mostly complete day.
MIN_FIRST_HOUR = 100.0
MIN_HOURS_PRESENT = 20
# Output bounds.
OUTPUT_FLOOR = 0.01
OUTPUT_CEILING = 1e9


def parse_instant(text: str) -> datetime:
    """An ISO 8601 UTC instant such as 2026-11-19T00:00:00Z."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def release_day(comp: dict) -> dict[int, float]:
    """A comparable's own release day as {hour 1..24: viewers}, hour 1 starting at its day_start."""
    try:
        start = parse_instant(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return {}
    counts: dict[int, float] = {}
    for point in comp.get("hourly") or []:
        try:
            when = parse_instant(point["from"])
            viewers = float(point["viewers"])
        except (KeyError, TypeError, ValueError):
            continue
        offset = int((when - start).total_seconds() // 3600)
        if 0 <= offset < 24:
            counts[offset + 1] = max(1.0, viewers)
    return counts


def is_target_echo(comp: dict) -> bool:
    """The target game's own pre-release history is shipped as a comparable, but is not a launch."""
    label = f"{comp.get('name') or ''} {comp.get('unlock') or ''}"
    return "Grand Theft Auto VI" in label or "target-history" in label or "Not a launch" in label


def is_console_only(comp: dict) -> bool:
    """A console launch with no PC release: the family this forecast is for."""
    platforms = comp.get("platforms") or ""
    return "PC" not in platforms and ("PS5" in platforms or "PS4" in platforms or "Xbox" in platforms)


def size_weights(first_hour: float, sizes: list[float]) -> list[float]:
    """Gaussian weights over log launch size, shifted so the nearest comparable weighs 1.0."""
    anchor = math.log(first_hour)
    squared = [(math.log(size) - anchor) ** 2 for size in sizes]
    nearest = min(squared)
    return [math.exp(-0.5 * (value - nearest) / (SIZE_BANDWIDTH * SIZE_BANDWIDTH)) for value in squared]


def weighted_median(values: list[float], weights: list[float]) -> float:
    """The value at which the running weight first reaches half of the total."""
    total = sum(weights)
    if total <= 0.0:
        return statistics.median(values)
    running = 0.0
    for value, weight in sorted(zip(values, weights)):
        running += weight
        if running >= total / 2.0:
            return value
    return max(values)


def interquartile_sigma(values: list[float]) -> tuple[float, int]:
    """A normal-consistent sigma from the inter-quartile range, with the sample size."""
    count = len(values)
    ordered = sorted(values)
    low = ordered[count // 4]
    high = ordered[(3 * count) // 4]
    return max(IQR_FLOOR, (high - low) / 1.349), count


class LaunchShape:
    """The log-multiples of a launch day, as a central multiple and a spread per hour."""

    def __init__(self, centre: dict[int, float], spread: dict[int, float]):
        self._centre = centre
        self._spread = spread

    @classmethod
    def fit(cls, comparables: list[dict] | None, first_hour: float) -> "LaunchShape | None":
        launches: list[dict[int, float]] = []
        consoles: list[dict[int, float]] = []
        for comp in comparables or []:
            if is_target_echo(comp):
                continue
            counts = release_day(comp)
            if 1 not in counts or counts[1] < MIN_FIRST_HOUR or len(counts) < MIN_HOURS_PRESENT:
                continue
            launches.append(counts)
            if is_console_only(comp):
                consoles.append(counts)
        if not launches:
            return None

        weights = size_weights(first_hour, [counts[1] for counts in launches])
        largest = max(counts[1] for counts in launches)
        beyond = max(0.0, math.log(first_hour) - math.log(largest))

        centre: dict[int, float] = {}
        spread: dict[int, float] = {}
        for hour in range(2, 25):
            present = [(counts, weight) for counts, weight in zip(launches, weights) if hour in counts]
            multiples = [math.log(counts[hour] / counts[1]) for counts, _ in present]
            centre[hour] = weighted_median(multiples, [weight for _, weight in present]) if multiples else 0.0

            measured = [math.log(counts[hour] / counts[1]) for counts in consoles if hour in counts]
            if len(measured) < 3:
                measured, size = multiples, len(multiples)  # too few console launches this hour
            else:
                size = len(measured)
            sigma, sample = interquartile_sigma(measured) if len(measured) >= 3 else (0.30, 1)
            sigma = min(sigma, 1.0) * CONSOLE_SHRINK
            sample = max(1, size)
            variance = (
                sigma * sigma
                + (sigma / math.sqrt(sample)) ** 2
                + (HORIZON_RATE * math.sqrt(hour - 1)) ** 2
                + (EXTRAPOLATION_RATE * beyond) ** 2
            )
            spread[hour] = min(max(math.sqrt(variance), SIGMA_FLOOR), SIGMA_CEILING)
        return cls(centre, spread)

    def quantiles(self, hour: int, first_hour: float) -> list[float]:
        """The five log-viewer values for one hour, before rounding and clamping."""
        centre = self._centre.get(hour, 0.0)
        spread = self._spread.get(hour, BASE_SIGMA * math.sqrt(hour - 1))
        return [first_hour * math.exp(centre + z * spread) for z in Z]


def half_up(value: float) -> float | int:
    """To two decimals, halves rounded up, an integer when the result is whole (as JS writes it)."""
    scaled = value * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    result = whole / 100.0
    return int(result) if result.is_integer() else result


def tidy(values: list[float]) -> list[float | int]:
    """Clamp a row into (0, 1e9], force it non-decreasing, and round it to two decimals."""
    row: list[float | int] = []
    previous = 0.0
    for raw in values:
        value = raw if math.isfinite(raw) else (OUTPUT_CEILING if raw > 0 else OUTPUT_FLOOR)
        value = min(max(value, OUTPUT_FLOOR), OUTPUT_CEILING)
        value = max(value, previous)
        rounded = half_up(value)
        row.append(rounded)
        previous = float(rounded)
    return row


def persistence(hour: int, first_hour: float) -> list[float]:
    """The house baseline: flat at the first hour, spread widening like a random walk."""
    spread = BASE_SIGMA * math.sqrt(hour - 1)
    return [first_hour * math.exp(z * spread) for z in Z]


def first_hour_print(case: dict) -> float:
    """Hour 1's count, with fallbacks that never raise."""
    for row in case.get("known") or []:
        if isinstance(row, dict) and row.get("hour") == 1:
            try:
                return max(1.0, float(row["viewers"]))
            except (KeyError, TypeError, ValueError):
                break
    history = case.get("history") or []
    if history:
        try:
            return max(1.0, float(history[-1]["viewers"]))
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return 1.0


def forecast(case: dict) -> dict:
    """The case's answer: exactly hours 2..24, each with five quantiles."""
    anchor = first_hour_print(case)
    try:
        shape = LaunchShape.fit(case.get("comparables"), anchor)
    except Exception:
        shape = None
    hours = []
    for hour in range(2, 25):
        raw = shape.quantiles(hour, anchor) if shape else persistence(hour, anchor)
        hours.append({"hour": hour, "q": tidy(raw)})
    return {"hours": hours}


def fallback() -> dict:
    """A valid answer for a case that could not be read at all, so the run still writes a file."""
    return {"hours": [{"hour": hour, "q": tidy(persistence(hour, 100000.0))} for hour in range(2, 25)]}


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        try:
            answer = forecast(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            answer = fallback()
        text = json.dumps(answer, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / f"{path.stem}.out").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
