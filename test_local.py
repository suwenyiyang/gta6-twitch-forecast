#!/usr/bin/env python3
"""Self-test for main.py (GTA VI Twitch forecast, make.fun bounty).

Run from the repository root:
    python3 test_local.py [--kit /path/to/gta6-twitch-ccu-forecast-01-kit]

Checks (no network, standard library only):
  1. Synthetic case: builds a small case in the documented
     "makefun/twitch-ccu-case/v1" format, runs main.py on it, and
     validates the output strictly (23 rows, hours 2..24 each once,
     five quantiles per row, finite, > 0, <= 1e9, non-decreasing,
     <= 65,536 bytes, single top-level key "hours").
  2. Determinism: two runs on the same input write byte-identical files.
  3. Runtime: each practice/synthetic case finishes well inside 8 s.
  4. Leave-one-out skill check (only if the competition kit is found):
     for each console-only comparable in the kit's practice cases, hold
     it out, forecast it from the remaining comparables with the exact
     shipped forecast_case(), and score pinball loss on the log scale
     against its real release-day hours; the same for the house
     persistence baseline. This is the honest out-of-sample estimate
     of the model on the launch family GTA VI belongs to.

Exit code 0 when every check passes; non-zero otherwise.
"""

import argparse
import contextlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]
TAUS = [0.05, 0.25, 0.5, 0.75, 0.95]

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("  ok  " if condition else " FAIL ") + message)
    if not condition:
        FAILURES.append(message)


@contextlib.contextmanager
def scratch_dir():
    """A throwaway directory that also works where tempfile.mkdtemp() cannot.

    tempfile.mkdtemp() creates the directory with a 0700 ACL, which a restricted
    Windows process (a sandbox, a service account, some CI runners) cannot create
    children in. A plain os.makedirs() under the system temp directory does, so it
    is tried first and a directory beside the repository is the last resort.
    """
    candidates = [Path(tempfile.gettempdir()), ROOT.parent, ROOT]
    for base in candidates:
        path = base / f"gta-forecast-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            path.mkdir(parents=True)
        except OSError:
            continue
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)
        return
    raise RuntimeError(f"no writable scratch directory among {[str(c) for c in candidates]}")


def discover_kit(explicit: str | None) -> Path | None:
    """The competition kit: --kit, else $MAKEFUN_KIT, else a sibling of the repo."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("MAKEFUN_KIT")
    if env:
        return Path(env)
    for candidate in (
        ROOT.parent / "gta6-twitch-ccu-forecast-01-kit",
        ROOT / "gta6-twitch-ccu-forecast-01-kit",
        Path("/tmp/gta-kit/gta6-twitch-ccu-forecast-01-kit"),
    ):
        if (candidate / "input").is_dir():
            return candidate
    return None


def load_main():
    spec = importlib.util.spec_from_file_location("forecast_main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinball(y: float, q: float, tau: float) -> float:
    d = math.log(max(1.0, y)) - math.log(max(1e-9, q))
    return 2.0 * max(tau * d, (tau - 1.0) * d)


def validate_output(data: dict, label: str) -> None:
    """The grader's shape rules, reported as one check per case."""
    problems: list[str] = []
    if not (isinstance(data, dict) and list(data.keys()) == ["hours"]):
        problems.append(f"top-level keys are {list(data)[:4] if isinstance(data, dict) else type(data).__name__}, not exactly ['hours']")
        check(False, f"{label}: " + "; ".join(problems))
        return
    hours = data["hours"]
    if not isinstance(hours, list) or len(hours) != 23:
        problems.append(f"{len(hours) if isinstance(hours, list) else type(hours).__name__} rows, not 23")
        check(False, f"{label}: " + "; ".join(problems))
        return
    seen = []
    for row in hours:
        if not isinstance(row, dict) or set(row.keys()) != {"hour", "q"}:
            problems.append(f"row keys {sorted(row) if isinstance(row, dict) else type(row).__name__}, not exactly hour+q")
            continue
        seen.append(row["hour"])
        q = row["q"]
        if not isinstance(q, list) or len(q) != 5:
            problems.append(f"hour {row['hour']}: {len(q) if isinstance(q, list) else type(q).__name__} quantiles, not 5")
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 < v <= 1e9 for v in q):
            problems.append(f"hour {row['hour']}: quantiles outside (0, 1e9] or not finite: {q}")
        if not all(q[i] <= q[i + 1] for i in range(4)):
            problems.append(f"hour {row['hour']}: quantiles decrease: {q}")
    if seen != list(range(2, 25)):
        problems.append(f"hours are {seen[:3]}..{seen[-3:]}, not 2..24 each once, in order")
    if problems:
        check(False, f"{label}: " + "; ".join(problems))
    else:
        check(True, f"{label}: valid — 23 rows, hours 2..24, five non-decreasing quantiles in (0, 1e9]")


def synthetic_case() -> dict:
    """A small case in the documented format, with a console-only and a
    PC-simultaneous comparable so the curve code has something to chew on."""
    def hourly(day, values):
        from datetime import datetime, timedelta
        base = datetime.fromisoformat(day.replace("Z", "+00:00"))
        return [
            {"from": (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "viewers": v, "channels": 100 + i}
            for i, v in enumerate(values)
        ]

    console_day = [50000, 42000, 38000, 37000, 40000, 46000, 52000, 58000,
                   64000, 70000, 78000, 86000, 94000, 102000, 110000, 118000,
                   125000, 132000, 138000, 140000, 136000, 130000, 122000, 115000]
    pc_day = [200000, 190000, 180000, 175000, 178000, 185000, 195000, 205000,
              215000, 225000, 235000, 245000, 255000, 265000, 275000, 285000,
              295000, 300000, 298000, 290000, 280000, 265000, 250000, 235000]
    return {
        "schema": "makefun/twitch-ccu-case/v1",
        "id": "synthetic-01",
        "game": {"name": "Grand Theft Auto VI", "twitch_id": "1867279146"},
        "day_start": "2026-11-19T00:00:00Z",
        "checkpoint": "2026-11-19T01:00:00Z",
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "known": [{"hour": 1, "from": "2026-11-19T00:00:00Z",
                   "until": "2026-11-19T01:00:00Z", "viewers": 80000,
                   "channels": 1500, "reads": 60}],
        "forecast": [{"hour": h,
                      "from": f"2026-11-19T{h - 1:02d}:00:00Z",
                      "until": f"2026-11-19T{h:02d}:00:00Z"}
                     for h in range(2, 25)],
        "history": [{"at": "2026-11-18T23:59:00Z", "viewers": 42000, "channels": 900}],
        "history_hourly": [],
        "streams": None,
        "categories_hourly": None,
        "comparables": [
            {"name": "Console Game A", "day_start": "2024-01-15T00:00:00Z",
             "unlock": "Consoles midnight local", "platforms": "PS5 only",
             "source": "synthetic", "hourly": hourly("2024-01-15T00:00:00Z", console_day)},
            {"name": "Console Game B", "day_start": "2024-03-10T00:00:00Z",
             "unlock": "Consoles midnight local", "platforms": "PS5, Xbox Series X|S",
             "source": "synthetic",
             "hourly": hourly("2024-03-10T00:00:00Z", [int(v * 0.7) for v in console_day])},
            {"name": "PC Game C", "day_start": "2024-05-20T00:00:00Z",
             "unlock": "PC simultaneous midnight GMT", "platforms": "PC, PS5",
             "source": "synthetic", "hourly": hourly("2024-05-20T00:00:00Z", pc_day)},
            # Target history: must be ignored by the model.
            {"name": "Grand Theft Auto VI", "day_start": "2026-08-27T00:00:00Z",
             "unlock": "Not a launch: pre-release history",
             "platforms": "PS5, Xbox Series X|S", "source": "synthetic",
             "hourly": hourly("2026-08-27T00:00:00Z", [1000] * 24)},
        ],
        "notes": "Synthetic self-test case; not a real launch.",
    }


def run_main(input_dir: Path, output_dir: Path) -> float:
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = str(input_dir)
    env["MAKEFUN_OUTPUT_DIR"] = str(output_dir)
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-P", "-s", str(ROOT / "main.py")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    check(proc.returncode == 0, f"main.py exits 0 (rc={proc.returncode})")
    return elapsed


def loo_skill_check(main, kit: Path) -> None:
    """Leave-one-out pinball loss on console-only comparables, using the
    shipped forecast_case() with the held-out launch removed."""
    print("Leave-one-out skill check (kit practice cases):")
    total_loss, total_n, total_cov = 0.0, 0, 0
    base_loss, base_n = 0.0, 0
    launches = 0
    for case_path in sorted((kit / "input").glob("p*.in")):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        comps = case["comparables"]
        usable = []
        for comp in comps:
            if main._is_target_history(comp):
                continue
            hours = main.release_day_hours(comp)
            if 1 in hours and hours[1] >= 100 and len(hours) >= 20:
                usable.append((comp, hours))
        console = [(c, h) for c, h in usable if main._is_console_only(c)]
        for held_comp, held_hours in console:
            launches += 1
            others = [c for c, _ in usable if c is not held_comp]
            pseudo = {
                "known": [{"hour": 1, "viewers": held_hours[1]}],
                "forecast": [{"hour": h} for h in range(2, 25)],
                "comparables": others,
            }
            forecast = {row["hour"]: row["q"] for row in main.forecast_case(pseudo)["hours"]}
            p1 = held_hours[1]
            for h in range(2, 25):
                if h not in held_hours:
                    continue
                for tau, q in zip(TAUS, forecast[h]):
                    total_loss += pinball(held_hours[h], q, tau)
                    total_n += 1
                if forecast[h][0] <= held_hours[h] <= forecast[h][4]:
                    total_cov += 1
                spread = 0.15 * math.sqrt(h - 1)
                for tau, z in zip(TAUS, Z):
                    base_loss += pinball(held_hours[h], p1 * math.exp(z * spread), tau)
                    base_n += 1
    if launches == 0:
        print("  (no console-only comparables found; skipped)")
        return
    loss = total_loss / total_n
    base = base_loss / base_n
    print(f"  console launches scored (LOO): {launches}")
    print(f"  model pinball loss:   {loss:.4f}")
    print(f"  baseline pinball loss:{base:.4f}")
    print(f"  90% coverage:         {total_cov / (23 * launches):.2f} (honest target ~0.90)")
    check(loss < base, f"model beats house baseline out-of-sample ({loss:.4f} < {base:.4f})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", default=None,
                        help="path to the gta6-twitch-ccu-forecast-01 kit (default: $MAKEFUN_KIT, "
                             "else a sibling directory of this repository)")
    args = parser.parse_args()
    main_mod = load_main()

    print("== 1. Synthetic case ==")
    with scratch_dir() as tmp:
        in_dir, out_dir = tmp / "in", tmp / "out"
        in_dir.mkdir()
        (in_dir / "synthetic-01.in").write_text(
            json.dumps(synthetic_case()), encoding="utf-8"
        )
        elapsed = run_main(in_dir, out_dir)
        check(elapsed < 8.0, f"synthetic case runs in {elapsed:.2f}s (< 8s limit)")
        out_path = out_dir / "synthetic-01.out"
        check(out_path.exists(), "synthetic-01.out written")
        raw = out_path.read_bytes()
        check(len(raw) <= 65536, f"output <= 65,536 bytes ({len(raw)})")
        validate_output(json.loads(raw), "synthetic-01.out")
        first_run = raw
        # Determinism: run again, expect byte-identical output.
        elapsed2 = run_main(in_dir, out_dir)
        check((out_dir / "synthetic-01.out").read_bytes() == first_run,
              "two runs on the same case are byte-identical")
        # Sanity: the median curve should dip early and peak in the evening.
        data = json.loads(first_run)
        med = {row["hour"]: row["q"][2] for row in data["hours"]}
        p1 = 80000
        print(f"  median ratios: h2={med[2]/p1:.2f}x h5={med[5]/p1:.2f}x "
              f"h12={med[12]/p1:.2f}x h19={med[19]/p1:.2f}x h24={med[24]/p1:.2f}x")
        check(med[3] < p1, "median dips below hour 1 in the early hours")
        check(med[19] > p1, "median rises above hour 1 by the evening peak")

    kit = discover_kit(args.kit)
    if kit is not None and (kit / "input").is_dir():
        print(f"== 2. Kit practice cases, one sandbox per case ==  [{kit}]")
        with scratch_dir() as tmp:
            for pid in ["p00", "p01", "p02", "p03"]:
                case_path = kit / "input" / f"{pid}.in"
                if not case_path.is_file():
                    check(False, f"{pid}.in present in the kit")
                    continue
                # The grader runs one case per sandbox: only that case's file is in /input.
                in_dir, out_dir = tmp / f"in-{pid}", tmp / f"out-{pid}"
                in_dir.mkdir()
                shutil.copy2(case_path, in_dir / f"{pid}.in")
                elapsed = run_main(in_dir, out_dir)
                check(elapsed < 8.0, f"{pid}: runs in {elapsed:.3f}s (< 8s per case)")
                out_path = out_dir / f"{pid}.out"
                check(out_path.exists(), f"{pid}.out written")
                if not out_path.exists():
                    continue
                raw = out_path.read_bytes()
                check(len(raw) <= 65536, f"{pid}.out is {len(raw)} bytes (<= 65,536)")
                validate_output(json.loads(raw), f"{pid}.out")
                run_main(in_dir, out_dir)
                check(out_path.read_bytes() == raw, f"{pid}: two runs on the same case are byte-identical")
        print("== 3. Leave-one-out skill ==")
        loo_skill_check(main_mod, kit)
    else:
        where = "no --kit given and none found beside the repository" if kit is None else f"not found at {kit}"
        print(f"== kit {where}: skipping practice-case checks ==")
        print("   pass --kit <path> or set MAKEFUN_KIT to the unpacked gta6-twitch-ccu-forecast-01-kit")

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s)")
        for failure in FAILURES:
            print(" - " + failure)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
