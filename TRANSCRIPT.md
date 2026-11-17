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
