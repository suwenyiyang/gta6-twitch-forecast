#!/usr/bin/env python3
"""Self-test for the forecast program in this repository.

    python3 test_local.py [--kit <path to the unpacked gta6-twitch-ccu-forecast-01 kit>]

The kit is looked for at $MAKEFUN_KIT, then beside this repository, then in /tmp/gta-kit. With no
kit the practice-case and skill checks are skipped and the rest still runs.

What is checked, all offline and with the standard library only:

  1. A synthetic case in the documented "makefun/twitch-ccu-case/v1" shape: the program exits
     cleanly, writes <id>.out, and the file passes every rule the grader applies -- one top-level
     key "hours", exactly 23 rows for hours 2..24 in order, five finite quantiles per row above
     0 and at most 1e9, non-decreasing, at most 65,536 bytes. Two runs must be byte-identical.
     The forecast must also behave like a launch day: a dip below the first hour early, a peak
     above it in the evening.

  2. Each of the kit's practice cases p00..p03, each run in a sandbox of its own with only that
     one case file in the input directory, exactly as the grader runs it: the same shape rules,
     under 8 seconds, and byte-identical output from two runs.

  3. A leave-one-out skill check on the kit's console-only comparables, the launch family this
     forecast is for. Each is held out in turn and forecast from the rest by the program's own
     forecast function, then scored with the contest's pinball loss on the log scale against its
     real release-day hours. The house persistence baseline is scored on the same hours, and the
     program must beat it. This is an out-of-sample estimate, not a practice score: the practice
     answers are the server's.

  4. Robustness: malformed and extreme cases still produce a valid file and a clean exit, because
     an invalid case scores as if every quantile were the first hour's count and takes no prize.

Exit status is 0 when every check passes, 1 otherwise.
"""

import argparse
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
TAUS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.645, -0.674, 0.0, 0.674, 1.645)

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


def load_program():
    """Import main.py as a module, without needing it on sys.path."""
    spec = importlib.util.spec_from_file_location("forecast_program", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scratch_dir():
    """A temporary directory that also works where tempfile.mkdtemp() cannot.

    mkdtemp creates its directory with a 0700 ACL, which a restricted Windows process (a sandbox,
    a service account, some CI runners) cannot create children in; a plain makedirs under the
    system temp directory can, so that is tried first, with the repository's parent as a fallback.
    """
    import contextlib

    @contextlib.contextmanager
    def manager():
        for base in (Path(tempfile.gettempdir()), ROOT.parent, ROOT):
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
        raise RuntimeError("no writable scratch directory")

    return manager()


def find_kit(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    candidates = []
    if os.environ.get("MAKEFUN_KIT"):
        candidates.append(Path(os.environ["MAKEFUN_KIT"]))
    candidates += [
        ROOT.parent / "gta6-twitch-ccu-forecast-01-kit",
        ROOT / "gta6-twitch-ccu-forecast-01-kit",
        Path("/tmp/gta-kit/gta6-twitch-ccu-forecast-01-kit"),
    ]
    for candidate in candidates:
        if (candidate / "input").is_dir():
            return candidate
    return None


def shape_problems(payload, label: str) -> list[str]:
    """Every reason the grader would call this output invalid, if any."""
    problems: list[str] = []
    if not isinstance(payload, dict) or list(payload) != ["hours"]:
        return [f"{label}: top level is not an object with the single key 'hours'"]
    rows = payload["hours"]
    if not isinstance(rows, list) or len(rows) != 23:
        return [f"{label}: not 23 rows"]
    seen = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"hour", "q"}:
            problems.append(f"{label}: a row's keys are not exactly hour and q")
            continue
        seen.append(row["hour"])
        q = row["q"]
        if not isinstance(q, list) or len(q) != 5:
            problems.append(f"{label}: hour {row['hour']} has no five quantiles")
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in q):
            problems.append(f"{label}: hour {row['hour']} has a non-finite quantile")
        elif not all(0 < v <= 1e9 for v in q):
            problems.append(f"{label}: hour {row['hour']} has a quantile outside (0, 1e9]")
        if not all(q[i] <= q[i + 1] for i in range(4)):
            problems.append(f"{label}: hour {row['hour']} quantiles decrease")
    if seen != list(range(2, 25)):
        problems.append(f"{label}: hours are not 2..24, each once, in order")
    return problems


def run_program(input_dir: Path, output_dir: Path) -> tuple[int, float, str]:
    """Run main.py the way the grader does, and time it."""
    environment = dict(os.environ, MAKEFUN_INPUT_DIR=str(input_dir), MAKEFUN_OUTPUT_DIR=str(output_dir))
    started = time.monotonic()
    finished = subprocess.run(
        [sys.executable, "-P", "-s", str(ROOT / "main.py")],
        env=environment, capture_output=True, text=True, timeout=120,
    )
    elapsed = time.monotonic() - started
    return finished.returncode, elapsed, finished.stderr


def synthetic_case() -> dict:
    """A case in the documented shape, with one console and one PC-simultaneous launch."""

    def curve(day: str, values: list[int]) -> list[dict]:
        base = __import__("datetime").datetime.fromisoformat(day + "T00:00:00+00:00")
        return [
            {"from": (base + __import__("datetime").timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "viewers": value, "channels": 100 + i}
            for i, value in enumerate(values)
        ]

    rising = [50000] + [40000 + 1200 * i for i in range(23)]
    flat = [200000] + [190000 - 1500 * i for i in range(23)]
    return {
        "schema": "makefun/twitch-ccu-case/v1",
        "id": "synthetic-01",
        "game": {"name": "Grand Theft Auto VI", "twitch_id": "1867279146"},
        "day_start": "2026-11-19T00:00:00Z",
        "checkpoint": "2026-11-19T01:00:00Z",
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "known": [{"hour": 1, "from": "2026-11-19T00:00:00Z", "until": "2026-11-19T01:00:00Z",
                   "viewers": 80000, "channels": 1500, "reads": 60}],
        "forecast": [{"hour": h,
                      "from": f"2026-11-19T{h - 1:02d}:00:00Z",
                      "until": f"2026-11-19T{h:02d}:00:00Z"} for h in range(2, 25)],
        "history": [{"at": "2026-11-18T23:59:00Z", "viewers": 42000, "channels": 900}],
        "history_hourly": [],
        "streams": None,
        "categories_hourly": None,
        "comparables": [
            {"name": "Console Launch A", "day_start": "2024-01-15T00:00:00Z",
             "unlock": "Consoles midnight local", "platforms": "PS5 only", "source": "synthetic",
             "hourly": curve("2024-01-15", rising)},
            {"name": "Console Launch B", "day_start": "2024-03-10T00:00:00Z",
             "unlock": "Consoles midnight local", "platforms": "PS5, Xbox Series X|S", "source": "synthetic",
             "hourly": curve("2024-03-10", [int(v * 0.7) for v in rising])},
            {"name": "PC Launch C", "day_start": "2024-05-20T00:00:00Z",
             "unlock": "PC simultaneous", "platforms": "PC, PS5", "source": "synthetic",
             "hourly": curve("2024-05-20", flat)},
            {"name": "Grand Theft Auto VI", "day_start": "2026-08-27T00:00:00Z",
             "unlock": "Not a launch: pre-release history", "platforms": "PS5",
             "source": "synthetic", "hourly": curve("2026-08-27", [1000] * 24)},
        ],
        "notes": "Synthetic self-test case; not a real launch.",
    }


def pinball(actual: float, quantile: float, tau: float) -> float:
    """The contest's item score."""
    delta = math.log(max(1.0, actual)) - math.log(max(1e-9, quantile))
    return 2.0 * max(tau * delta, (tau - 1.0) * delta)


def practice_cases(kit: Path):
    for path in sorted((kit / "input").glob("p*.in")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def skill_check(program, kit: Path) -> None:
    """Leave-one-out on console-only launches, against the house persistence baseline."""
    total_model = total_base = 0.0
    items = covered = launches = 0
    for _, case in practice_cases(kit):
        usable = []
        for comp in case["comparables"]:
            if program.is_target_echo(comp):
                continue
            counts = program.release_day(comp)
            if 1 in counts and counts[1] >= 100 and len(counts) >= 20:
                usable.append((comp, counts))
        held_out = [(comp, counts) for comp, counts in usable if program.is_console_only(comp)]
        for target, truth in held_out:
            launches += 1
            others = [comp for comp, _ in usable if comp is not target]
            pseudo = {
                "known": [{"hour": 1, "viewers": truth[1]}],
                "comparables": others,
            }
            forecast = {row["hour"]: row["q"] for row in program.forecast(pseudo)["hours"]}
            anchor = truth[1]
            for hour in range(2, 25):
                if hour not in truth:
                    continue
                for tau, quantile in zip(TAUS, forecast[hour]):
                    total_model += pinball(truth[hour], quantile, tau)
                    items += 1
                if forecast[hour][0] <= truth[hour] <= forecast[hour][4]:
                    covered += 1
                for tau, z in zip(TAUS, Z):
                    total_base += pinball(truth[hour], anchor * math.exp(z * 0.15 * math.sqrt(hour - 1)), tau)
    if not launches:
        print("  (no console-only comparables in this kit; skill check skipped)")
        return
    model_loss = total_model / items
    base_loss = total_base / items
    print(f"  console-only launches held out: {launches} ({items // 5} scored hours)")
    print(f"  pinball loss, this program:     {model_loss:.4f}")
    print(f"  pinball loss, house baseline:   {base_loss:.4f}")
    print(f"  90% coverage:                   {covered / (23 * launches):.2f} (an honest band gives ~0.90)")
    check(model_loss < base_loss, f"program beats the house baseline out of sample ({model_loss:.4f} < {base_loss:.4f})")


def robustness(program) -> None:
    """Cases the program must survive: it may forecast badly, but never crash or emit junk."""
    def base():
        return {
            "known": [{"hour": 1, "viewers": 50000}],
            "comparables": [],
        }

    broken = {
        "no comparables": base(),
        "no known row": {"comparables": []},
        "first hour zero": {"known": [{"hour": 1, "viewers": 0}], "comparables": []},
        "first hour at the cap": {"known": [{"hour": 1, "viewers": 1e9}], "comparables": []},
        "first hour negative": {"known": [{"hour": 1, "viewers": -5}], "comparables": []},
        "first hour a string": {"known": [{"hour": 1, "viewers": "52000"}], "comparables": []},
        "comparables null": {"known": [{"hour": 1, "viewers": 50000}], "comparables": None},
        "malformed comparable": {"known": [{"hour": 1, "viewers": 50000}],
                                 "comparables": [{"name": "x", "hourly": "nope"}]},
        "empty case": {},
    }
    for label, case in broken.items():
        try:
            payload = program.forecast(case)
            problems = shape_problems(payload, label)
        except Exception as exc:  # noqa: BLE001 - any raise is the failure this checks for
            problems = [f"{label}: raised {type(exc).__name__}: {exc}"]
        check(not problems, f"survives '{label}'" + ("" if not problems else f" ({problems[0]})"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-test for the forecast program.")
    parser.add_argument("--kit", default=None,
                        help="the unpacked gta6-twitch-ccu-forecast-01 kit (default: $MAKEFUN_KIT, "
                             "then a sibling directory of this repository)")
    arguments = parser.parse_args()
    program = load_program()

    print("== 1. synthetic case ==")
    with scratch_dir() as work:
        source, target = work / "in", work / "out"
        source.mkdir()
        (source / "synthetic-01.in").write_text(json.dumps(synthetic_case()), encoding="utf-8")
        code, elapsed, stderr = run_program(source, target)
        check(code == 0, f"exits cleanly (exit {code})" + (f" {stderr.strip()[:200]}" if code else ""))
        check(elapsed < 8.0, f"runs in {elapsed:.3f} s (limit 8 s per case)")
        written = target / "synthetic-01.out"
        check(written.is_file(), "writes synthetic-01.out")
        if written.is_file():
            raw = written.read_bytes()
            check(len(raw) <= 65536, f"output is {len(raw)} bytes (limit 65,536)")
            payload = json.loads(raw)
            problems = shape_problems(payload, "synthetic-01.out")
            check(not problems, "output passes every shape rule" + ("" if not problems else f": {problems[0]}"))
            run_program(source, target)
            check(written.read_bytes() == raw, "two runs on the same case are byte-identical")
            medians = {row["hour"]: row["q"][2] for row in payload["hours"]}
            print("   median ratios: " + " ".join(
                f"h{hour}={medians[hour] / 80000:.2f}x" for hour in (2, 5, 12, 19, 24)))
            check(medians[3] < 80000, "median dips below the first hour early in the day")
            check(medians[19] > 80000, "median peaks above it in the evening")

    print("== 2. robustness ==")
    robustness(program)

    kit = find_kit(arguments.kit)
    if kit is None:
        print("== 3. practice cases skipped: no kit found (pass --kit, or set MAKEFUN_KIT) ==")
    else:
        print(f"== 3. practice cases, one sandbox per case ==  [{kit}]")
        with scratch_dir() as work:
            for case_id in ("p00", "p01", "p02", "p03"):
                original = kit / "input" / f"{case_id}.in"
                if not original.is_file():
                    check(False, f"{case_id}.in is in the kit")
                    continue
                source, target = work / f"in-{case_id}", work / f"out-{case_id}"
                source.mkdir()
                shutil.copy2(original, source / f"{case_id}.in")
                code, elapsed, stderr = run_program(source, target)
                check(code == 0, f"{case_id}: exits cleanly (exit {code})")
                check(elapsed < 8.0, f"{case_id}: runs in {elapsed:.3f} s (limit 8 s)")
                written = target / f"{case_id}.out"
                check(written.is_file(), f"{case_id}: writes {case_id}.out")
                if not written.is_file():
                    continue
                raw = written.read_bytes()
                check(len(raw) <= 65536, f"{case_id}: output is {len(raw)} bytes (limit 65,536)")
                problems = shape_problems(json.loads(raw), f"{case_id}.out")
                check(not problems, f"{case_id}: output passes every shape rule" +
                      ("" if not problems else f": {problems[0]}"))
                run_program(source, target)
                check(written.read_bytes() == raw, f"{case_id}: two runs are byte-identical")
        print("== 4. leave-one-out skill ==")
        skill_check(program, kit)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
