# GTA VI launch-day Twitch forecast — make.fun bounty entry

Forecasts how many people will be watching Grand Theft Auto VI live on
Twitch in each hour of its release day (2026-11-19 UTC), as five
quantiles per hour, from the count in the day's first hour alone.

- Bounty: [Forecast GTA VI's launch-day Twitch viewers hour by hour](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
- Reward: $250 USDC, 20 places paid (1st $50, 2nd $35, 3rd $25, 4th–10th $10, 11th–20th $7).
  A prize needs a pinball loss **strictly below the best house entry**
  (persistence baseline or house AI model, whichever is lower).
- Deadline: **2026-11-19 01:00 UTC (09:00 Beijing)** — the end of hour 1.
- Entry: `main.py` (Python 3.13, standard library only) in the repo root.

## Exact I/O format (verified from the competition kit)

Sources: the kit's `RULES.txt` and `starter/main.py`
(`curl -sSL "https://make.fun/c/gta6-twitch-ccu-forecast-01/kit" | tar xz`),
and `GET https://make.fun/api/competitions/gta6-twitch-ccu-forecast-01/requirements`.
Nothing about the format below is guessed.

**Input** — `/input/<id>.in` (or `$MAKEFUN_INPUT_DIR`), UTF-8 JSON, schema
`"makefun/twitch-ccu-case/v1"`:

| Field | Meaning |
|---|---|
| `id`, `game{name, twitch_id}`, `day_start`, `checkpoint`, `levels` | Case identity; `levels` is `[0.05, 0.25, 0.5, 0.75, 0.95]` |
| `known` | Hour 1's print: one row `{hour: 1, from, until, viewers, channels, reads}` |
| `forecast` | The 23 rows to forecast: `{hour, from, until}` for hours 2–24 |
| `history` | Every valid point before the checkpoint, oldest first, `{at, viewers, channels}` (minute reads in the final; the historical source's points in practice) |
| `history_hourly` | The same as whole-hour means |
| `streams` | Top 20 live streams at the last valid read (null in practice) |
| `categories_hourly` | Twitch's top 20 categories once an hour (null in practice) |
| `comparables` | Past big launches' hourly counts, 14 days before to 3 days after their release day (SullyGnome hourly averages): 38 in a practice case, 42 in the final; each `{name, day_start, unlock, platforms, source, hourly: [{from, viewers, channels}]}`. One entry in every case is GTA VI's own pre-release category history (2026-08-27 premiere → 2026-11-17) — it is **not** a launch. |
| `notes` | Where the case's data comes from |

**Output** — `/output/<id>.out` (or `$MAKEFUN_OUTPUT_DIR`), at most 65,536 bytes:

```json
{"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]}, ..., {"hour": 24, "q": [...]}]}
```

Exactly 23 rows, hours 2–24 each once, one top-level key (`hours`),
five quantiles per row, each finite, `> 0`, `<= 1e9`, non-decreasing.
Anything else (missing file, wrong shape, crash, over 8 s) is an
**invalid case**: it is scored as if every quantile equalled hour 1's
print, and an entry with any invalid case takes no prize.

**Scoring** — mean pinball loss on the log scale over every scored
hour × quantile: `2 · max(τ·d, (τ−1)·d)` with `d = ln(y) − ln(q)`,
`y` the hour's print. A forecast whose five quantiles are equal scores
exactly its mean absolute log error. Lower is better; ties go to the
faster program. The final is scored on the frozen recording of
2026-11-19; practice is scored on four past launches (Marvel's
Wolverine 2026-09-15, Red Dead Redemption 2 2018-10-26, God of War
Ragnarök 2022-11-09, Marvel's Spider-Man 2 2023-10-20) and pays nothing.

**Runtime limits** — at most 8 s, 1 CPU, 1024 MB per case; deterministic
(two runs on the same case must write the same forecast); no network;
nothing installed before the run.

## Model

The case itself ships the training data: 38 past launches with full
hourly curves. The program estimates the forecast from those curves at
run time; no curve shape is hard-coded from memory.

1. **Anchor.** `p1` = the case's known hour-1 print (00:00–01:00 UTC).
   Everything is a ratio to `p1`, so the forecast adapts to whatever
   hour 1 actually was.
2. **Median curve.** For each usable comparable, divide its release-day
   hourly viewers by its own hour-1 print and take logs. The **median**
   log-ratio over **all** usable comparables is the median curve. Its
   shape, measured on the kit's data: a dip to ~0.75× in hours 2–5
   (deep night in Europe, US not yet unlocked), a slow climb as the US
   unlocks (east coast 05:00 UTC, west coast 08:00 UTC), a peak of
   ~2.0–2.4× in hours 19–20 (US afternoon/evening + EU evening prime
   time), easing to ~2.0× by hour 24. Using all comparables (n ≈ 37)
   instead of only console-only ones (n = 7) makes the median far more
   stable, and leave-one-out scoring prefers it.
3. **Spread.** The honest uncertainty for a console-only rolling-midnight
   launch is much narrower than the spread across all launch types
   (PC-simultaneous launches have a totally different shape). So the
   log-spread comes from the **console-only** comparables (platforms
   with no PC — the family GTA VI belongs to, PS5 + Xbox Series X|S):
   the inter-quartile robust sigma of their log-ratios, plus the
   standard error of the median, plus a horizon term:
   `s_h = clamp(sqrt(σ² + (σ/√n)² + (0.08·√(h−1))²), 0.25, 1.0)`.
   Quantiles are lognormal around the median:
   `q_τ(h) = p1 · exp(m_h + z_τ · s_h)`, `z = [-1.645, -0.674, 0, 0.674, 1.645]`.
4. **Exclusions.** GTA VI's own pre-release history (tier
   `target-history`) is not a launch and is excluded; so is any
   comparable without a usable release day (hour-1 print < 100 viewers
   or fewer than 20 of 24 hours present).
5. **Safety.** If anything about a case is unusable, the program still
   writes a valid forecast (the house persistence baseline) instead of
   crashing.

## Validation (leave-one-out, honest out-of-sample)

Measured with `test_local.py`, which scores the **shipped**
`forecast_case()` on the kit's comparables: each console-only launch is
held out in turn, forecast from the remaining comparables, and scored
against its real release-day hours. (The same 7 console-only launches
appear in each of the 4 practice cases, so 28 scorings of 7 launches.)

| | Pinball loss (log scale) | 90% coverage |
|---|---|---|
| This model | **0.2176** | 0.93 |
| House persistence baseline (same launches) | 0.3245 | — |

For reference, the house baseline scores ~0.387 on the four practice
cases (per the kit's RULES.txt), with typical miss ~82.8% and 90%
coverage ~0.783. The final is a different day — a practice-era edge is
evidence, not a promise. The two baselines that must be beaten on the
final are the persistence baseline and the house AI model (unknown
score; it is made 2026-11-18 23:00 UTC).

Local test results (2026-11-17, `python3 test_local.py`):
synthetic-case format checks pass; two runs byte-identical; 4 practice
cases run in 0.17 s total (limit 8 s per case); **ALL CHECKS PASSED**.

## Assumptions and limits (labelled as such)

- **Assumption:** GTA VI's launch-day shape resembles past console-only
  rolling-midnight launches (God of War Ragnarök, Spider-Man 2, Ghost
  of Tsushima, Ghost of Yōtei, Stellar Blade, …). GTA VI is far bigger
  than any of them; the model anchors on hour 1, so scale is handled,
  but if its *shape* is unprecedented (e.g. hour 1 already contains a
  global simultaneous surge), the curve will be off. The spread is the
  hedge for that.
- **Assumption:** the median ratio curve is stable across launch years
  (2018–2026) and the Twitch audience's diurnal rhythm has not
  structurally changed. No external data was used to set any parameter;
  every number in the model is estimated from the comparables inside
  the case file at run time.
- **Not used:** the case's `history` (pre-release minute reads),
  `streams` (top-20 streams at the checkpoint) and `categories_hourly`.
  They carry hype/competition signal, but no validated way to turn them
  into a ratio adjustment was found; adding an unvalidated adjustment
  would risk the score. A future version could test whether pre-release
  category viewers predict the hour-1-to-peak ratio.
- **Practice ≠ final:** practice answers come from SullyGnome hourly
  averages; the final's prints are means of minute reads. Close, not
  identical. Also, the final's comparables number 42 (practice: 38).
- The synthetic case in `test_local.py` is invented data for format
  testing only; its median ratios (e.g. h12 1.72×) are **not** model
  validation — the LOO numbers above are.

## Run locally

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
python3 test_local.py            # full self-test (kit path optional)
python3 test_local.py --kit /path/to/gta6-twitch-ccu-forecast-01-kit
```

Get the kit (no sign-in needed):
```sh
curl -sSL "https://make.fun/c/gta6-twitch-ccu-forecast-01/kit" | tar xz
```

## Submit (the user must do these steps)

1. Create a **public GitHub repo owned by your own GitHub account,
   created after the competition opened (2026-11-17 06:00 UTC)**,
   OSI-approved license (MIT file included), with `main.py` in the root.
2. Push this code. Do **not** include the kit, practice cases, or any
   secret in the repo.
3. Enter with the kit CLI (`node cli/makefun.mjs ...` from the unpacked
   kit) and upload the **agent-session transcript bundle** that built
   this entry — see `TRANSCRIPT.md`. At most 10 submissions a day,
   20 in all; the last submission before the deadline is the final entry.
4. A reward is claimed in USDC on Solana after the result is posted.
