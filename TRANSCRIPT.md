# Build transcript (build log)

> **Important — read before submitting.** This file is a build log written
> by the agent that built the entry, so the user has a record of what was
> done. It is **not** a substitute for the transcript bundle the bounty
> requires. The competition's requirements say every entry must upload
> the AI coding-agent sessions that built it (made by the kit CLI,
> `node cli/makefun.mjs bundle`), and an entry whose sessions are
> "handed over" as a reconstruction is ranked but **takes no paid place**
> (`handed_over_places: none`). To be eligible for a prize, build (or
> re-run/verify the build of) this entry in a harness whose sessions the
> kit can bundle — e.g. Claude Code, Codex, Cursor, or the Muse app —
> and upload that bundle with the submission. Do not present this file
> as a session record.

## What was built

- **Bounty:** Forecast GTA VI's launch-day Twitch viewers hour by hour
  (`gta6-twitch-ccu-forecast-01`), $250, deadline 2026-11-19 01:00 UTC.
- **Built on:** 2026-11-17 (UTC) by a Muse Spark subagent, on the user's
  instruction to fully automate a make.fun entry for this bounty.
- **Deliverables in this repo:** `main.py` (the entry program),
  `README.md` (model + format documentation), `LICENSE` (MIT),
  `test_local.py` (self-test), this file.

## How it was built

1. Read the bounty page
   (https://make.fun/bounty/gta6-twitch-ccu-forecast-01) and
   https://make.fun/skill.md (v1.5.199).
2. Downloaded the competition kit
   (`curl -sSL "https://make.fun/c/gta6-twitch-ccu-forecast-01/kit"`)
   and read `RULES.txt`, `starter/main.py`, and the four practice cases
   (`input/p00.in`–`p03.in`). The exact input/output format, scoring
   (pinball loss on the log scale), runtime limits and baseline were
   taken from those files — not guessed.
3. Analysed the 38 comparables shipped in the practice cases: computed
   per-hour median log-ratios to hour 1 and robust (IQR) spreads, for
   all launches and for the console-only subset (Ghost of Yōtei, Saros,
   Death Stranding 2, Final Fantasy VII Rebirth, The Last of Us Part II,
   Ghost of Tsushima, Stellar Blade). Found the console-only spread is
   far tighter (σ ≈ 0.25–0.64) than the all-launches spread (σ up to
   ~1.55), while the median curves are similar in shape.
4. Ran leave-one-out validation on the console-only launches for
   several model variants (median source, spread source, horizon
   widening). Selected: median from all usable comparables, spread from
   console-only comparables + median standard error + 0.08·√(h−1)
   horizon term, clamped to [0.25, 1.0].
5. Wrote `main.py` (standard library only, deterministic), `README.md`,
   `LICENSE`, `test_local.py`, and this log.
6. Ran `python3 test_local.py`: synthetic-case format checks, two-run
   byte-identical determinism, 4 practice cases in 0.17 s total, and
   leave-one-out scoring — **ALL CHECKS PASSED**. LOO pinball loss
   0.2176 vs house persistence baseline 0.3245 on the same launches;
   90% coverage 0.93.

## What was NOT done (needs the user)

- No GitHub repository was created, no make.fun account or Solana
  wallet was created, and nothing was submitted — those need the user's
  own GitHub account and wallet.
- No real agent-session bundle exists for this build (see the notice at
  the top). Re-run the build in a recorded harness before submitting
  if the entry is to be prize-eligible.
- The entry was not graded by make.fun's server; the numbers above are
  local leave-one-out estimates on the kit's comparables, not practice
  scores and not a promise about the final.

## 2026-11-17, later — verification pass (second session)

A second agent session re-downloaded the kit, re-read `RULES.txt` and
`starter/main.py`, and verified this repository against them. What it
found and changed:

- **Format.** `main.py` was already correct on the documented contract
  (reads every `*.in` in `$MAKEFUN_INPUT_DIR` / `/input`, writes
  `/output/<id>.out`, 23 rows, five non-decreasing quantiles in
  `(0, 1e9]`, under 65,536 bytes, no network, standard library only).
  Verified on p00–p03, each run in its own sandbox with only its case.
- **Two real defects, fixed.**
  1. `baseline_quantiles()` — the fallback used when a case has no
     usable comparables — had no `1e9` clamp, so a (hypothetical)
     hour-1 print near 1e9 produced quantiles above the cap and an
     invalid case. Clamping is now shared by both quantile builders
     (`_clamp_row`).
  2. If a case's `forecast` list repeated an hour or held an
     out-of-range one, the output grew to 24 rows. The output now
     always carries exactly hours 2–24, whatever that list says.
  A 15-case robustness pass (missing `known`, hour 1 of 0 / 1e9 /
  negative / a string, null and zero viewers, malformed comparables, a
  case that is a JSON list, …) now yields a valid `.out` and exit 0
  for every one.
- **Model.** Leave-one-out over the kit's comparables showed the
  unweighted median could be improved a lot by weighting comparables
  by how close their launch size is to the target's (bandwidth 1.3 in
  log viewers), since launch size drives shape. On the 7 console-only
  launches: 0.2176 → 0.1814; over all 37 useful launches: 0.842 →
  0.432. Shading the console IQR by 0.85 (it is measured
  unconditionally, the median is now conditional) takes the console
  score to **0.1748** with 0.95 coverage. The house persistence
  baseline scores 0.3245 on the same launches.
- **test_local.py.** Now runs each practice case in its own sandbox
  like the grader, checks two-run byte-identity per case, aggregates
  the shape check per case, discovers the kit automatically
  (`--kit`, `$MAKEFUN_KIT`, or a sibling directory), and no longer
  depends on `tempfile.mkdtemp()` (whose 0700 ACL fails under a
  restricted Windows process).
- **Not changed:** the I/O contract, the LICENSE, the submission
  instructions, and the `history` / `streams` / `categories_hourly`
  fields, which remain unused and documented as such.

## 2026-11-17, later still — the code was written again in one session

make.fun's own pre-submit check (`node cli/makefun.mjs check`) failed
`thinking_reproduces`: only 21.2% of the entry's counted lines were
authored in an uploaded session (175 authored, 39 copied, 580 read, 28
absent against the 30% the rule asks for). The bulk of `main.py`,
`test_local.py` and the README had been written by the earlier build
session, and that session does not exist on this machine to upload: the
kit's `bundle` scans 39 harness stores and found only the verification
session, and a disk scan for session files from the build window found
nothing (the earlier client kept its sessions server-side).

The kit's own fix for that is "have the agent write that code itself in
the sessions you upload". So `main.py` and `test_local.py` were written
again from scratch by the verification session — a different
decomposition, naming and comments, the same estimator. The rewrite
was checked against the previous build's output for p00–p03: the four
forecasts are **byte-identical**, and the leave-one-out loss is
unchanged at 0.1748. `LICENSE` also gained the `SPDX-License-Identifier:
MIT` line that `license_osi` requires.

Everything since — the verification, the two defect fixes, the
size-weighted model, the rewrite and the commit — is in the uploaded
session.

## 2026-11-17, second entry — the rolling unlock

The entry above (commit 4945a272, submission 01a1e98d) stays on the
board and is not replaced. A second entry fixes one mechanism the first
one got wrong by construction.

The largest launches in the case are PC premieres that unlocked
everywhere at once, so their 00:00–01:00 UTC hour already contained the
Americas. GTA VI is a console release that unlocks at midnight local:
at 00:00–01:00 UTC only Europe and the UK are open, the US east coast
follows at 05:00 and the west at 08:00. Where the size kernel leans on
those simultaneous launches — which is exactly what it does for a
seven-figure first hour — the day comes out too flat.

The fix measures that lean rather than assuming it: the share of kernel
weight sitting on launches that unlocked simultaneously. The central
multiple is pulled that far towards the console-only shape, capped at
25%, and the band is skewed with it — wider above the median, tighter
below, at most 6.25% and 5% respectively, because the skew applies to a
tilt already capped. The cap was not chosen by hand: leave-one-out on
the kit's console-only comparables gives 0.1748 without the tilt, 0.1696
at 0.25, 0.1707 at 0.40 and 0.1735 at 0.50, so 0.25 is where the tilt
still pays. No practice answer was used at any point.

A second entry is a *replacement*, not an extra ticket: the rules make the
last submission before the deadline the entry that is graded, so this one
supersedes 4945a272. If it ever looks worse, the earlier commit can be
submitted again before the deadline and becomes the final entry once more,
at the cost of one more submission.




