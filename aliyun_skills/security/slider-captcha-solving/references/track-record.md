# Track record: what this solver actually solved, across the whole corpus

Evidence for the SKILL's `### Bound the claim` subsection. Compiled 2026-10-02
from `/tmp/run_*.log` (the trial-and-error corpus for the throwaway
`jev_captcha.py`) plus `/tmp/tcaptcha/outcome.json` (the rebuilt tool).

## Corpus

```
total run logs : 56          (run_1..5, A, B, F1-F2, G1-G6, H1-H6,
                               I1-I10, J1-J3, K1, L1-L6, M1, N1-N3,
                               R1-R3, S1-S4, T1-T4)
solved (ec=0)  : 23  -> 41%
failed         : 33
```

errorCode distribution over the whole corpus:

| code | occurrences | reading |
|---|---:|---|
| `0` | 23 | accepted, `ticket` issued |
| `12` | 23 | IP / fingerprint rate-limit (risk control) |
| `50` | 5 | refused after repeated failures |

Failure modes across the 33 losses: **35 logs** carry an empty `ticket:""`
(placeholder / risk window — the dominant cause), **10** carry
`IFRAME NOT FOUND` (pre-prefetch proxy CONNECT drops, batch L/N), **5** carry a
Python traceback (incl. the `PlaywrightContextManager.stop` teardown bug and
the corrupt-image `UnidentifiedImageError`).

## The 23 successes

| run | utc | attempt | note |
|---|---|---:|---|
| B | 04:59 | 1 | |
| R1 | 05:04 | 2 | round 1 scattered → refresh, round 2 consensus |
| T2 | 05:25 | 1 | |
| T3 | 05:29 | 1 | |
| T4 | 05:30 | 3 | `50`, `50`, then `0` |
| G1 | 06:05 | 2 | refresh-then-drag |
| G2 | 06:05 | 1 | |
| G3 | 06:07 | 1 | |
| G4 | 06:08 | 1 | |
| H1 | 06:25 | 1 | |
| H2 | 06:25 | 2 | |
| H3 | 06:27 | 1 | |
| H4 | 06:28 | 1 | |
| H5 | 06:31 | 3 | image missing → refresh → drag |
| H6 | 06:32 | 1 | |
| I1 | 06:49 | 2 | |
| I2 | 06:49 | 1 | |
| I3 | 06:50 | 1 | |
| I4 | 06:51 | 1 | |
| I8 | 06:58 | 1 | |
| I9 | 06:59 | 2 | |
| I10 | 07:00 | 1 | |
| M1 | 08:13 | 1 | direct egress, last success of the day |

Attempt spread: 16 first-try, 5 second-try, 2 third-try.
Window: 04:59 → 08:13 UTC. Everything started returning empty tickets after
M1 — see [`rate-limit-and-risk-control.md`](rate-limit-and-risk-control.md).

## Vendor coverage — solved vs never attempted

| challenge | status |
|---|---|
| **Tencent TCaptcha** (drag-to-gap slider) on MiniMax login iframe | **23 solves** |
| reCAPTCHA · hCaptcha · Cloudflare Turnstile · GeeTest · Arkose/FunCaptcha · DataDome · dingding · `captcha.qq` | **0 attempts — no log mention anywhere** |

Silence in the logs is not coverage. The never-attempted half of the answer is
what keeps a "23 solves" claim honest.

## The rebuilt tool's own corpus (separate)

`/root/tcaptcha` (package `tcaptcha`) has run **twice** as of 2026-10-02 10:19
UTC: both stopped at the risk gate — `[risk] prehandle kind=placeholder` →
**exit 4**, `outcome.json {"ok": false, "attempts": 1, "risk_hold": true}`.
Zero solves of its own; do not fold its runs into the 56-run corpus, which was
produced by the old script.

## Tally commands (re-runnable)

```bash
ls /tmp/run_*.log | wc -l                          # corpus size
grep -l 'SUCCESS' /tmp/run_*.log | wc -l           # solved
grep -rhoE 'errorCode=?-?[0-9]+' /tmp/run_*.log | sort | uniq -c | sort -rn
grep -L 'SUCCESS' /tmp/run_*.log | xargs -n1 basename          # the 33 losses
for f in /tmp/run_*.log; do grep -q SUCCESS "$f" || continue
  printf '%s|%s|%s\n' "$(basename $f .log)" "$(date -u -r $f +%H:%M)" \
    "$(grep -o 'SUCCESS.*' "$f" | head -1)"; done             # the 23 successes
grep -rIioE 'recaptcha|h-captcha|turnstile|geetest|arkose|datadome' /tmp/run_*.log | sort -u
```

Pitfalls while tallying (both cost a wrong number the first time):
- `grep -L` piped to `wc -l` and `grep -c` inside a loop whose exit status is
  also used as a result silently report the *grep* status — print the tally
  from a script instead.
- The success grep must read `SUCCESS` from each run's **own** log; the
  outcome file is rewritten per run, so it cannot reconstruct a corpus.
