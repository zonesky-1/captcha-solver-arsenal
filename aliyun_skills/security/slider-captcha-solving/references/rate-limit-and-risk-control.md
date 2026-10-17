# TCaptcha `errorCode 12` — rate-limit / risk control

Evidence behind the `## Verify means reading the wire` section of the SKILL.
This file exists because the skill previously taught the opposite reading.

## The correction

`errorCode: "12"` was recorded as *"wrong answer (coordinate off)"*. It is not.
Two independent write-ups describe it as Tencent's **IP / fingerprint rate-limit
(risk-control)** code, and a 10-run batch where detection quality stayed constant
while the outcome flipped purely with the clock supports them.

### Sources (quoted)

**`github.com/2185359703/reserver` (tds/)**

> IP 风控：连续快速请求会触发 errorCode=12（限流）。建议请求间隔 ≥ 10 秒，单轮
> 测试 ≤ 10 次；被风控后冷却 10-30 分钟。

**`pj-one.github.io` — 腾讯天御 collect-vData 逆向**

> 腾讯 verify：IP/指纹级滚动窗口，约 50 发/10 分钟 → errorCode=12，脚本已自动
> 退避 65s … 风控期（placeholder）由 IP 触发，与 UA 无关（3 个 UA + 新 Session
> 实测均 placeholder），等自然解除或换出口 IP 后主滑块路径自动恢复。

Weaker, third-hand: several CSDN write-ups read 12 as "环境存在异常，系统判定为
自动化行为" (environment anomaly / automation detected) — a behaviour flag, still
not "your coordinate was wrong". One DeepWiki note claims incorrect *values* also
yield 12, so treat 12 as **ambiguous, rate-limit-first** rather than exclusively
rate-limit.

## The discriminator: `12` follows the clock, a coordinate error follows the image

Batch I, one process launch per row, identical build (I5 was a PIL decode crash,
unrelated to the captcha):

| Run | Result | Detail |
|---|---|---|
| I1 | ok | attempt 2 |
| I2 | ok | attempt 1, 279/279 |
| I3 | ok | attempt 1 |
| I4 | ok | attempt 1, 537/537/535 |
| I5 | crash | corrupt image bytes → `PIL.UnidentifiedImageError` |
| I6 | fail | `12` on three unrelated challenges |
| I7 | fail | `12` on three unrelated challenges |
| I8 | ok | attempt 1 |
| I9 | ok | attempt 2 |
| I10 | ok | attempt 1 |

7/10, and the three non-successes are two contiguous `12`-bursts plus one
non-captcha crash. Inside those bursts the consensus was *tight*:

| Run | a1 | a2 | a3 | code |
|---|---|---|---|---|
| I6 | 333/334/336 | 429/430/429 | 423/—/422 | `12` `12` `12` |
| I7 | 519/520/520 | 366/541/541 | 378/378/380 | `12` `12` `12` |

Three different challenges in a row, each independently located to within 3 px,
all refused — then the *next* runs with the same detector stack and comparable
agreement succeeded. A coordinate error cannot produce that temporal pattern; a
rolling rate window can.

Earlier rows recorded in this skill (`339/339`, `459/460`, `411/411/413`,
`285/286/285`) were all `12` too and were read as "consensus was wrong". That
reading is withdrawn for those rows. The decoy evidence in
`consistency-and-retry.md` still stands on its own visual/numeric measurements —
only the `12` attribution is retracted.

## What the widget does while flagged: placeholder

After the `I6`/`I7`/`J1` bursts, two consecutive runs got no captcha at all:

```
[captcha] IFRAME NOT FOUND [initial] frames=['https://account.minimax.io/unified-login']
```

Host frame only, no `turing.captcha.gtimg.com` child — exactly the
"风控期（placeholder）" state described above. This is **not** a selector bug;
retrying the selector will not fix it. Rule: if a run reports *no iframe at all*
right after a `12` burst, stop the batch and wait out the cooldown.

## Backoff tried, and what it cost

An `errorCode == "12"` branch was added: log, wait **70 s**, re-enter the flow,
`MAX_ATTEMPTS` raised 3 → 4.

Result: J1 hit `12` on **all four** attempts — 4 × 70 s ≈ 280 s of waiting did
not clear the window. So:

- 65–70 s (the documented auto-backoff) is a *first* backoff, not a guarantee.
- Budget the per-run `timeout` as `attempts × backoff + setup`. 4 × 70 s blew a
  330 s `timeout`; the SIGTERM killed Python mid-write and Playwright's driver
  died with `Error: write EPIPE / errno -32`. That stack trace is a consequence
  of the timeout, not a Playwright bug.
- On a second consecutive `12` after backoff, **stop the batch** and wait
  10–30 min rather than burning the remaining attempts.

## Pacing for future batches

- ≥ 10 s between `verify` requests; 20–25 s between separate runs is comfortable.
- ≤ 10 runs per round.
- Cool down 10–30 min after a flagged round. A fresh session or UA does not help —
  the flag is IP-scoped.
- A batch that hammers ~30 runs back-to-back trips the window *mid-batch*. The
  failures then land on time rather than difficulty, and read as algorithmic
  regressions when they are not.

## Rotating the egress IP — the throughput fix, but not a universal reset

The pacing rules buy safety; rotation buys throughput. Since the `12` flag is
IP-scoped, **one fresh egress IP per run** is the right first move for `12`
instead of waiting 10–30 min.

> **Correction (added after the L/N batches): rotation does NOT reliably clear
> the *placeholder* state, and the claim below that it "clears both … with no
> wait" is withdrawn for placeholder.** See "Two ways to be flagged" for the
> matrix. Treat rotation as a `12` tool and a transport tool; when prehandle
> hands out empty tickets from *every* IP including your own direct one, only
> time fixes it.

Recipe, validated on this target:

1. **Pool of authenticated `http://user:pass@host:port` proxies.** Screen them
   before trusting one:
   - `curl -x "$p" https://api.ipify.org` must echo the proxy's own IP
     (sampled 8/8 alive, each returning a *different* IP),
   - `curl -x "$p" -o /dev/null -w '%{http_code}' https://account.minimax.io/unified-login`
     → `200`,
   - same against `https://t.captcha.qq.com/cap_union_prehandle` → `200`.

   A proxy that reaches the internet but not the target is worse than none —
   it fails late, inside the browser, as a blank page.
2. **Inject at context scope**, so every request the widget makes (prehandle,
   image fetch, verify) leaves from that IP:

   ```python
   parts = urllib.parse.urlsplit(os.environ["JEV_PROXY"])   # or JEV_PROXY chosen per run
   if parts.hostname and parts.port:
       proxy_cfg = {"server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}",
                    "username": urllib.parse.unquote(parts.username),
                    "password": urllib.parse.unquote(parts.password)}
   ctx = b.new_context(viewport=..., proxy=proxy_cfg, user_agent=...)
   ```

   Split the credentials out of the URL — Playwright does not accept
   `http://user:pass@host` in `server`. `proxy=None` (env unset) keeps the
   direct-connection behaviour, so the same script serves both modes.
3. **Rotate and attribute.** `shuf` the pool, one proxy per run; `rm -f` the
   previous run's outcome artifact before starting; log `host:port` on every
   launch so a failed run can be tied to its exit IP.

Caveat: pacing still applies *per exit IP* — don't burst many verifies through
one member of the pool either.

## Proxy screening does not catch the browser's concurrency failure

Batch L: 6 proxies, all screened, all logging `[proxy] host:port`, all failing
at the same line —

```
[tos] button clicked=True
[captcha] IFRAME NOT FOUND [initial] frames=['https://account.minimax.io/unified-login']
```

Cause was not risk control. Through the same proxy:

| Request | `curl` | Chromium |
|---|---|---|
| `turing.captcha.qcloud.com/TCaptcha.js` | `200`, 136 901 bytes | `net::ERR_EMPTY_RESPONSE` |
| `turing.captcha.qcloud.com/tcaptcha-frame.ba387dd1.js` | `200`, 230 804 bytes | (never reached) |
| `turing.captcha.gtimg.com/1/template/drag_ele.html` | `200`, 63 617 bytes | (never reached) |

Chromium opens ~15 CONNECTs at page load; this proxy class drops some of them
under that burst, and the casualty happened to be the widget script — no
`TCaptcha.js`, no widget, no child frame, indistinguishable from a flagged IP
unless the request-failure log is read. Screenings in step 1 above all passed,
because they measure *sequential reachability*, not the browser's concurrency
profile.

**Fix (validated):** pre-fetch those four assets through the proxy at launch
with `urllib`/`curl` and serve them from `page.route` fulfilment; also
`route.abort()` third-party analytics to reduce CONNECT pressure. Result: 6/6
`IFRAME NOT FOUND` → 3/3 `frame ok`.

Supporting traps:

- In `sync_playwright`, `page.route` handlers must be **`def`, not `async def`**
  — an unawaited coroutine handler hangs every request and `goto` times out,
  which looks exactly like the site being down.
- The blocklist must exclude the target's own reporter domain: aborting
  `data.hailuo.ai` (first-party telemetry) hung the login form for two runs;
  dropping it fixed both.

## Two ways to be flagged — and why the second one does not yield to rotation

| State | Symptom | Clears with |
|---|---|---|
| no child frame at all | `frames=[<host only>]` right after a `12` burst | cooldown (and often rotation) |
| **frame mounts, no ticket** | `prehandle_resp` = `state:1, ticket:""`, `src_2=template/new_placeholder.html`, so the widget never posts `verify` → `errorCode=None`, `[verify-reqs] 0` | **time × reduced volume** — rotation alone did not do it |

The second state was invisible until now because it looks like "detector ran but
verify didn't answer". Batch N (after the asset fix) produced it on three
never-used proxies; the isolation matrix then produced the same empty ticket
from every cell:

| Variable changed | `ticket` |
|---|---|
| direct IP — 15 min earlier this had returned a real `tr03_…` ticket and `errorCode 0` | `""` |
| 5 further proxies, other subnets, never used before | `""` |
| UA + viewport + timezone + locale changed | `""` |
| never-registered email address | `""` |

Cumulative attempt volume over the session was the one variable *not* isolated,
so the defensible claim is **at least partly volume × time keyed, not purely
IP-keyed** — contradicting the "换出口 IP … 恢复" reading from the pj-one source
for this state (that source may be describing the no-frame state instead).

**Operational consequence:** when tickets are empty on every IP including
direct, stop all traffic and schedule a delayed retest (one-shot, ~40 min, 1
direct run + 1 proxy run). Every probe during the window is another attempt
that extends it, so a 10-proxy "clean IP hunt" spends attempts on a hypothesis
the matrix already weakened.

## Inconclusive experiment: replaying a captured prehandle URL

To test IP-scoping cheaply, the captured `prehandle` URL was replayed with
`curl` through 11 different IPs — all returned `placeholder`. That proved
nothing: the capture carried `sess=` empty and a `tkid` minted in an earlier
session, so an expired/foreign key yields placeholder from *any* origin. A
replay can only test transport, never risk scoping. Use a live browser run per
IP, or don't claim the variable was isolated.

## Batch mechanics that produced misleading results

- **`pkill -f "<pattern>"` can kill your own shell.** When the pattern string
  appears in the command line you are running it from (very easy in a
  compound `... ; pkill -f jev_captcha.py ; ...`), the shell matches itself,
  dies with `-15`, and everything after it silently never runs — it looks like
  the command "stopped early". Use a pattern that cannot match itself
  (`pkill -f "[j]ev_captcha"`) or match on the interpreter instead.
- **Long batches do not fit in `execute_code`** (300 s hard cap). A 3-run batch
  with 70 s backoffs overran it and was killed mid-flight. Drive batches from
  `terminal` with `background=true` + `notify_on_complete=true`, one log file
  per run.
- **A summarizer reading a fixed artifact path reports the *previous* run.**
  Placeholder runs write no outcome file at all, so two early-exit runs printed
  the prior run's numbers verbatim — identical detector values across "runs" is
  the tell. `rm -f` the artifact before each run, or assert its mtime is newer
  than that run's start time.
- **Size each run's timeout from its worst case**, `attempts × backoff + setup`.
  Hitting it shows up as Playwright `Error: write EPIPE`, which reads like a
  driver bug and is not.
