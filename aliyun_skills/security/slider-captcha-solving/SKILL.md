---
name: slider-captcha-solving
description: Solve slider/jigsaw CAPTCHAs programmatically against live sites — TCaptcha, NetEase, Geetest, Aliyun INPAINTING. Use when a flow blocks automation with a drag-to-fit challenge, when gap detection must be validated against server tolerance, or when a verify code needs triage (TCaptcha 12 = IP rate limit; Aliyun F015 = IP flag, F014 = wrong coordinate on a quadratic drag widget). Covers challenge capture, multi-detector gap detection with consensus voting, coordinate scaling, in-browser drag, challenge re-entry, rate-limit cooldowns, typed-decision arbitration of weak evidence, keeping the decision pipeline inside the challenge lifetime (spawn detector→decision→file as a child process; agent turn latency misses every window), shipping the solver credential-free with a fidelity proof, porting to a new TCaptcha host, and honest N× reliability runs (fail-closed per-run scoring, proxy health-check + retry ladder). Complements hunt-captcha-bypass.
---

# Solve slider CAPTCHAs

A slider challenge is solved in three owned stages: **host captures and detects,
a decision layer picks, the host executes**. Keep them separate. Detection is
deterministic code; only the *choice among candidates* is a judgment call.

## Pipeline

1. **Reach the challenge.** Drive a real browser (Playwright/CDP), not raw HTTP.
   CAPTCHA widgets are almost always cross-origin iframes; resolve the frame by
   URL substring (`gtimg`, `captcha`) rather than by index.
2. **Capture the challenge from network**, not from the DOM: intercept the
   `prehandle` response (often JSONP — strip the callback shell) and the image
   downloads. The background and the foreground sprite arrive as separate
   requests; keep the raw bytes.
3. **Extract the movable piece** from the foreground sprite using `sprite_pos` +
   `size_2d`. Then cut it out of the sprite by its alpha channel.
4. **Detect the gap** with at least two independent detectors and vote.
5. **Convert logical → screen pixels** using the *rendered* box of the background
   element, never the image's natural size.
6. **Execute the drag** with real mouse events (down → many small moves → up),
   ease-in-out with jitter, a brief hold at each end.
7. **Verify from the wire**: read the `verify` request the widget itself
   submits and the response body. That is the only ground truth.

## Detect with a consensus, not a single shot

Run independent detectors and require agreement before acting:

- **Alpha-mask NCC** — template-match the piece's opaque pixels against the
  background. Robust, but lockable onto a decoy silhouette.
- **Edge × contour correlation** — Canny the background, cross-correlate with the
  piece's contour outline. Fast, but picks up any blob with a similar outline.
- **Threshold + contour area filter** — cheap; keep only contours whose bounding
  box is near the piece's own size *and* row.

Then cluster the x-coordinates within a few pixels. **Agreement is only
meaningful between *structurally independent* detectors; a scattered vote is a
rejection.** Rejecting a challenge and retrying is a legitimate, cheap outcome —
a fresh image costs one round trip, a wrong drag costs a failure flag.

### Pitfall: two shape matchers can be wrong *together*

NCC (piece alpha) and Canny×contour (piece outline) both ask "which place in the
background looks like this piece?" — so they frequently land on the **same decoy**
and "agree" with each other. Observed trials where a confident 2-of-2 shape
agreement came back rejected:

| Trial | NCC | Canny | Independent 3rd detector | Outcome |
|---|---|---|---|---|
| A | 339 | 339 | 534 | `errorCode 12` |
| B | 459 | 460 | 315 | `errorCode 12` |
| C | 411 | 411 | 413 (all three agree) | `errorCode 12` |

> **Correction — these rows are *not* evidence that the consensus was wrong.**
> `errorCode 12` was later identified as Tencent's IP rate-limit / risk-control
> code, not a refused coordinate
> ([`references/rate-limit-and-risk-control.md`](references/rate-limit-and-risk-control.md)).
> A later batch reproduced exactly this shape — three unrelated challenges in a
> row, 3-of-3 agreement within 3 px, all `12`, then immediate success once the
> window passed. Drop `12` from the case *for* independence; the decoy
> measurements two sections down are the real evidence for it.

The independence problem stands on that evidence: a dark detector is also a
"which place looks like the piece" detector, so agreement narrows risk without
removing it. Consequences:

- Never treat consensus as proof. Keep `refresh`/`abort` on the table even at
  p≈0.99, and keep the reject path when the vote is scattered.
- To get real independence, add a detector that answers a *different* question
  (e.g. "does the piece's y-row contain a discontinuity at this x?" via vertical
  projection or row-wise variance) rather than a fourth shape matcher.
- Rank correlation, not just adjacency: a third detector that merely reproduces
  the shape detectors' bias will make the vote *more* confident, not more correct.

### Pitfall: background images carry decoys

Many challenges paint a filled silhouette *into* the background alongside the
real cut-out hole. An edge detector cannot tell them apart by itself. This is
the single most common source of confident-but-wrong coordinates. Evidence:

- NCC said `x=213`, edge said `x=525` — 312 px apart on one challenge.
- Visual inspection showed the edge detector had locked onto the decoy.
- On a confirmed-bad challenge all three detectors read `x=285/286/285`, while
  visual inspection located the real hole at `x=440–540`.

Measured on that same image (logical coords, piece 120×120):

| Candidate | Outline-match strength | interior / ring brightness |
|---|---|---|
| decoy `x=285/286` | **45.3 M / 39.6 M** (highest) | **0.54** (opaque, dark) |
| hole `x=440…500` | 23.6–31.5 M | **1.07–1.14** (background runs through) |

So the decoy has the *stronger* outline — outline strength is anti-signal here,
and brightness contrast alone only weakly separates them (22.7 vs 34.3).

Vote across detectors, and reject when they disagree.

### Rejected approach: rank candidates by interior/brightness ratio

The tempting follow-up — "find every outline peak, then pick the one whose
interior matches the surrounding brightness, since a hole keeps the background
and a decoy is opaque" — was implemented and **lost 0/5 in live trials**
(all five override drags came back `errorCode 50`; three runs without the
override returned `errorCode 0` immediately after).

Two failure modes, both worth not re-deriving:

1. **Plain background also scores ratio ≈ 1.** Ranking *on the ratio* produced a
   false pick at `x=74`, next to the piece's start position, because any
   texture-free patch has interior ≈ ring. Filtering on ratio first and then
   ranking the survivors by outline strength fixes that specific bug but does
   not fix the overall record.
2. **The ratio does not beat the shape detectors empirically.** Every trial where
   it overrode a shape consensus failed; every trial where it stayed out of the
   decision path succeeded.

Keep such a detector as *evidence in the report*, not as an override. The general
rule: a detector that has not been validated against a known outcome must never
outrank one that has.

### Pitfall: element list order is meaningless

In TCaptcha-style `fg_elem_list`, the first entry is often a decorative bar
whose `init_pos` lies outside the image height — iterating `[0]` raises
`IndexError` or matches nothing. Select by capability instead: the element whose
`move_cfg.data_type` contains `DynAnswerType_POS`.

## Scale from the DOM, not from the image

The logical canvas, the natural image size, and the rendered box are three
different numbers. Observed on one live challenge:

| Quantity | Value |
|---|---|
| Logical canvas (`bg_elem_cfg.size_2d`) | 672 × 480 |
| Natural background image | 672 × 390 |
| Rendered box on screen | 340 × 242.84 |
| **Scale factor** | **340 / 672 = 0.505952** |

`drag_px = (gap_x - init_x) * (rendered_width / logical_width)`.

The canvas is taller than the image because the slider track sits *below* the
image area — which is also why a naive "find the element under the background"
handle search returns nothing.

## Retry by re-entering the flow, not by clicking refresh

A rejected challenge must be replaced by a **new one**, and the widget's own
refresh control does not provide one. Probe against the live TCaptcha widget
(clicking `.tc-action--refresh`, both element-click and raw coordinate-click at
its centre):

```
after load: prehandles = 1, images = 4
attempt 1 click -> prehandles 1 -> 1, images 4 -> 8
attempt 2 click -> prehandles 1 -> 1, images 4 -> 8
```

The image fetches double while the `prehandle` count never moves: the button
re-downloads the **same session's** images. Nothing was gained, and 30×300 ms of
polling was spent discovering it.

**Therefore the retry primitive is to re-run the entry path** — navigate to the
entry URL, redo the form steps, wait for the iframe and a fresh `prehandle`
again. Package that whole sequence as one re-entrant function and call it for
both the first attempt and every retry; it is the difference between "one bad
image ends the run" and "the run recovers".

### Pitfall: capture state leaks across challenge boundaries

The dominant cause of confident-but-nonsense detection is an **old image
paired with a new challenge**. Symptom: detectors scattered across the whole
track (observed `120 / 479 / 332` — an impossible spread for one image).

Rules:

- Before a retry, **wait until the `prehandle` request counter has advanced**
  past the value it had before you triggered the retry. Only then drop the stored
  background/foreground bytes and the stored response.
- Do not clear on a timer and do not clear before the new request is observed —
  either way a late response from the *old* challenge can repopulate the buffer.
- The frame handle may legitimately outlive the response. When polling, keep the
  frame you found and keep waiting for the response; only report "no iframe" when
  the frame itself is absent. Nulling the frame variable because the response
  was late turned a solvable run into a false "IFRAME NOT FOUND".
- But *genuinely* absent across a whole run is its own finding, not a selector
  bug. On a flagged IP TCaptcha serves a **placeholder** and the captcha child
  frame never mounts (`frames=[<host only>]`, no `turing.captcha.gtimg.com`).
  This followed a burst of `errorCode 12`. If a run reports no iframe at all
  right after `12` failures, stop the batch and wait out the 10–30 min cooldown
  — retrying the selector, or a fresh session/UA, will not help.
- `resp.body()` is not always readable. Record the image URL alongside the bytes
  and, if the body is missing after a beat, re-fetch through the API context
  (`ctx.request.get(url)`). One run came back with `images=[]` purely from this.
- Bytes that *do* arrive can still be truncated. Letting `PIL.Image.open` run
  unguarded on them raised `UnidentifiedImageError` and killed the whole run
  (exit 1, no outcome written). Treat a decode failure as a capture failure:
  catch it, log it, drop the stored buffers and re-enter the flow — it becomes a
  normal reject round instead of a dead run.
- Gate the loop on a bounded attempt count, and let a retry that cannot produce a
  new `prehandle` **exit immediately** rather than burning the remaining attempts
  on dead rounds.

A working layout: `detect → build candidates → decision model → execute/drag →
verify` inside a `for attempt` loop whose failure branch calls the re-entrant
entry function. With that structure in place the run went **10/10**, including
runs that needed two or three rounds of reject-then-re-enter.

## Verify means reading the wire, not the screenshot

The widget submits `verify` itself after a real drag. Capture:

- `ans` — the coordinate **the widget computed**. Compare it to your detected
  target; a few px of difference is normal and shows detection is within
  tolerance.
- the response body — `errorCode: "0"` plus a `ticket` is success.

Failure codes seen on the live target, and what each should change:

| Code | Reading | Action |
|---|---|---|
| `0` | accepted — `ticket` + `randstr` present | done |
| `12` | **IP / fingerprint rate-limit (risk control)** — *not* a wrong coordinate | pace and stop the batch; do not re-tune detection |
| `50` | rejected after repeated failures | do not keep dragging; back off or re-enter the flow cleanly |
| `None` — verify never sent at all (`[verify-reqs] 0`) | prehandle issued `ticket:""` + `src_2=template/new_placeholder.html`, so the widget has nothing to submit | read `prehandle_resp`, not the detector; see Caveat 2 below |

**`12` is the code that will fool you.** It reads exactly like "your answer was
refused", and this skill recorded it as *wrong coordinate* for several rounds of
trials. Two independent write-ups call it a rate limit, and a later batch proved
it empirically: three unrelated challenges detected to within 3 px each returned
`12` in a burst, then the same code path succeeded minutes later. Sources, the
run table and the pacing rules are in
[`references/rate-limit-and-risk-control.md`](references/rate-limit-and-risk-control.md).

Discriminate by what the failure follows:

- **Follows the image** — different challenges, plausible-but-wrong coordinates,
  succeeds after re-detection → the coordinate really was bad.
- **Follows the clock** — several *unrelated* challenges in a row all return `12`
  despite 3-of-3 agreement, and runs started later succeed with the same stack →
  rate limit. Stop, wait 10–30 min, come back.
- **Widget replaced by a placeholder** — two shapes, both unmistakable once you
  look for them: the captcha child frame never mounts at all
  (`frames=[<host only>]`, no `turing.captcha.gtimg.com`), **or** the frame
  mounts but prehandle returns an empty `ticket` and the widget never sends a
  verify request. The first follows a `12` burst and resolves with cooldown;
  the second is diagnosed from `prehandle_resp`, and the selector or a fresh
  session/UA will not bring a ticket back (see Caveat 2). Either way, waiting on
  the selector never helps.

Pacing that avoids the window entirely: ≥ 10 s between `verify` requests, ≤ 10
runs per round, 10–30 min cooldown after a flagged round (a fresh session or UA
does not help — the flag is IP-scoped). Budget a run's `timeout` as
`attempts × backoff + setup`; otherwise the timeout kills Playwright mid-write
and surfaces as a misleading `Error: write EPIPE`.

**Or skip the wait: rotate the egress IP.** Because the `12` flag is
IP-scoped, one fresh authenticated HTTP proxy per run usually clears the `12`
window with no cooldown at all. Read the two caveats below before trusting
rotation for the *placeholder* state, or before trusting a proxy that merely
passed liveness screening. Inject the proxy at `new_context` scope so the
widget's own prehandle / image / verify traffic all leaves from that IP:

```python
parts = urllib.parse.urlsplit(os.environ["JEV_PROXY"])
ctx = b.new_context(proxy={"server": f"{parts.scheme}://{parts.hostname}:{parts.port}",
                           "username": urllib.parse.unquote(parts.username),
                           "password": urllib.parse.unquote(parts.password)}, ...)
```

Parse `user:pass` out of the URL — Playwright rejects credentials embedded in
`server`. Validate the pool first (`curl -x "$p" https://api.ipify.org` for
liveness, then the target and `t.captcha.qq.com` must both return `200` through
it), log `host:port` on every launch so a failure can be attributed to an exit
IP, keep pacing per exit IP, and `rm -f` the previous run's outcome artifact
before the next run. Full recipe and the batch-mechanics pitfalls are in
[`references/rate-limit-and-risk-control.md`](references/rate-limit-and-risk-control.md).

### Caveat 1: the proxy is a second failure mode — `curl` proving a proxy works proves nothing about the browser

`IFRAME NOT FOUND` has **three** causes that look identical from the frame list,
so diagnose the transport before blaming risk control:

| Evidence | Cause | Fix |
|---|---|---|
| `frames=[<host only>]` **and** a `12` burst just before | IP flagged → placeholder | cooldown / rotate |
| `frames=[<host only>]` **and** `net::ERR_EMPTY_RESPONSE` on `turing.captcha.*` in the request/console log | proxy dropped the widget script | pre-fetch + serve the assets (below) |
| form steps fail / no `input[type=email]` | flow or selector problem | fix the entry path |

Observed: Chromium's concurrent CONNECTs through a cheap authenticated proxy get
dropped — `TCaptcha.js` came back `ERR_EMPTY_RESPONSE` in the page while
`curl -x "$proxy" https://turing.captcha.qcloud.com/TCaptcha.js` through the
*same* proxy returned `200` / 136 901 bytes. All 6 runs of the first proxy
batch failed identically on this — and the pool had passed the exact liveness
screening recommended above (ipify + target + `t.captcha.qq.com`, all `200`).
So **pool screening checks reachability, not the browser's concurrency
profile**; always read the request-failure log for `turing.captcha.*`, never the
frame list alone.

Fix — pre-fetch the four static assets through the proxy at launch and serve
them from memory (also block third-party trackers to cut CONNECT pressure).
Assets exist on **both** `turing.captcha.qcloud.com` and `turing.captcha.gtimg.com`
(`TCaptcha.js`, `tcaptcha-frame.<hash>.js`, `1/template/drag_ele.html`):

```python
def _route(route):                      # MUST be sync — see the pitfall below
    u = route.request.url
    if u in asset_cache:
        route.fulfill(status=200, body=asset_cache[u],
                      content_type="application/javascript" if u.endswith(".js") else "text/html")
    elif any(t in u for t in TRACK):    # analytics only — see the second pitfall
        route.abort()
    else:
        route.continue_()
pg.route("**/*", _route)
```

This turned 6/6 `IFRAME NOT FOUND` into 3/3 `frame ok`.

Three Playwright traps hit while building it:

- **The sync API needs a sync handler.** An `async def` passed to `page.route`
  under `sync_playwright` is never awaited — every request hangs, `goto` times
  out at 60 s, and it reads exactly like the site being down.
- **Do not block the target app's own reporter domain.** Blocking
  `data.hailuo.ai` (the host's first-party telemetry) hung the login form for
  two consecutive runs; removing it fixed both. Block third-party analytics, not
  the site's infrastructure.
- **`sync_playwright()` is a context manager, not the Playwright object.**
  `p = sync_playwright(); p.start()` returns the object; keeping `p` around and
  calling `p.stop()` in teardown raises `AttributeError: 'PlaywrightContextManager'
  object has no attribute 'stop'` — and since teardown runs *after* the result
  was computed, that traceback lands on top of an otherwise successful run and
  hides it. Store `self._pw = sync_playwright().start()` and launch from
  `self._pw.chromium`. Same species of bug: `__import__("importlib").metadata`
  is `AttributeError` even with the package installed —
  `__import__("importlib.metadata", fromlist=["version"]).version("playwright")`
  or a plain `import importlib.metadata`.

### Caveat 2: rotation did *not* clear placeholder — that state is partly volume × time keyed

After the transport fix, three never-used proxies each mounted the widget but
prehandle returned `ticket:""` + `src_2=template/new_placeholder.html`, so the
widget **never submitted verify** (`errorCode=None`, zero verify requests — see
the table above). A follow-up matrix then produced the same empty ticket from
*every* cell:

| Variable changed | Result |
|---|---|
| direct exit IP (had worked 15 min earlier: real `tr03_...` ticket, `errorCode 0`) | `ticket=""` |
| 5 more proxies on other subnets, never used | `ticket=""` |
| different UA + viewport + timezone + locale | `ticket=""` |
| never-registered email address | `ticket=""` |

One variable was *not* isolated — cumulative attempt volume over the session —
so the honest reading is that this state is at least partly **time × volume
keyed, not purely IP-keyed**, and rotation alone cannot be relied on to clear
it.

Operational rule: when tickets are empty across *every* IP including direct,
**stop entirely and wait** — and schedule the retest instead of probing, because
every probe is another attempt that keeps the window alive. A one-shot delayed
retest (40 min; 1 direct run + 1 proxy run) is the right shape; scanning 10
proxies to "find a clean IP" is not, since that spends attempts on a hypothesis
the matrix had already weakened.

Note `50` is *not* automatically a rate limit: a run returned `50` twice and then
`0` on the next round inside the same session. Treat a code you have not
classified as "this answer was refused" and re-measure rather than guessing.

A screenshot showing a spinner or a vanished widget is suggestive, not proof.
Persist raw background/foreground bytes and the detection result *before* the
decision, so a failed run is still diagnosable.

### Pitfall: tally runs from the artifacts, not from what you remember

Reporting "N successes" from memory or from a file you may not have rewritten is
how a broken build looks green. Concrete traps hit while measuring:

- The outcome file is rewritten **inside** the loop. A run that dies mid-round
  leaves the *previous* run's file on disk, so two different runs can report
  identical numbers. Check the exit status and the process that wrote the file.
- **Fail closed on artifact freshness: `unlink` the outcome file before *every*
  run, not once at batch start.** An Nx harness deleted it once at t=0, then
  scored runs 4 and 5 — one quick init failure and one full `F015` — from run
  3's leftover file and reported them as PASS. A PASS is "artifact exists AND
  was written during this run AND contains the win condition"; no artifact
  from this run = FAIL, never an inherited PASS. Parse the win condition from
  the run's own stdout too (`"success":true`), not only from the file. Bake it
  into the *tool* as well, not just the harness: `tcaptcha` v1.1.0 unlinks
  verdict artifacts at the start of `solve()` (`_scrub_stale`), so an aborted
  run leaves only `run.log` and a harness that forgets to clean still cannot
  read a stale PASS.
- Reading only the **last** history entry misreports: on a run that gave up after
  three tries, the last entry is `{"attempt": 3, "error": "prehandle parse"}`,
  which prints as `jev=None ec=None` even though attempt 1 submitted a real drag.
  Iterate the whole history.
- `grep -c` in a shell loop whose exit status you also use as a result silently
  counts the *grep* exit code. Write the tally to a small script and print it.

Only claim a solve rate from `errorCode` values parsed out of the logs of runs
you actually executed under the current build.

### Bound the claim: coverage, corpus number, and what was never tried

When asked *"list captcha apa saja yang berhasil lu solve"*, answer in three
parts — every one read off artifacts, never off memory:

1. **Vendor + surface.** One row naming the challenge *and* where it was
   mounted: `Tencent TCaptcha (slider)` on the `MiniMax login` iframe
   (`turing.captcha.qcloud.com`). "I solve captchas" is not an answer.
2. **The corpus number, not the best batch.** The subset tally in the reference
   below (10/10 on the G/H batch) is true and misleading; the whole corpus was
   **56 run logs → 23 `errorCode=0` (41%)** — `0`×23, `12`×23, `50`×5,
   failure modes 35 empty-ticket / 10 `IFRAME NOT FOUND` / 5 tracebacks, last
   success 08:13 UTC. Quote both numbers *with their scope* and lead with the
   corpus one.
3. **The never-attempted list.** `grep -riE 'recaptcha|h-captcha|turnstile|geetest|arkose|datadome'`
   over the run logs returned zero hits → state it: reCAPTCHA, hCaptcha,
   Turnstile, GeeTest, Arkose, DataDome were never tried. Silence in the logs
   is not coverage; "we never tried X" is the honest second half of any
   capability answer.

**Session history is not evidence.** On a fresh profile `session_search` for
the same question returned 0 sessions — the answer lived in `/tmp/run_*.log`
and `outcome.json`. Search the session DB if it may hold history, but the claim
itself comes from files on disk. Full run table, failure-mode breakdown and the
exact tally commands are in
[`references/track-record.md`](references/track-record.md).

### Pitfall: filter capture by URL path, not substring

Monitoring endpoints embed the API path inside a query string — a
`cap_monitor?...data={"name":"...cap_union_new_verify"}` request matched a naive
substring filter and **overwrote the real verify response with `{}`**, making a
successful solve look like an empty failure. Parse the path and compare the last
segment.

Run tables, the refresh probe transcript and the detector measurements behind
these rules are in
[`references/consistency-and-retry.md`](references/consistency-and-retry.md);
the `errorCode 12` correction, its sources and the batch pacing rules are in
[`references/rate-limit-and-risk-control.md`](references/rate-limit-and-risk-control.md).

## Bounded retest runs: spend zero hits before you spend one

A scheduled retest of a flagged window usually arrives with a hard captcha-hit
budget and explicit STOP conditions (missing credentials, placeholder still
returning `ticket:""`, `errorCode 12`). The budget is consumed only by *live
captcha traffic*, so order the run so most outcomes are decided before any
traffic exists:

1. **Offline health check first.** Run the tool's own `doctor`/`check`
   subcommand (zero network) → then credential/proxy presence → only then a
   single bounded solve (`--max-attempts N`).
2. **If the tool's health check itself crashes, replicate it — never fix the
   tool mid-retest.** Read the check command's source, enumerate what it
   verifies, and reproduce each check with a `python -c` one-liner, labelling
   each OK/FAIL. Editing tool source during the window changes the thing being
   measured and destroys the report's "here is why the retest could not run".
3. **Distinguish a stale check from a missing dependency.** A check like
   `check("playwright", lambda: playwright.__version__)` FAILs with
   `AttributeError` while the package and its browsers are in fact installed —
   confirm with `importlib.metadata.version(...)` and a glob of
   `~/.cache/ms-playwright/*` before reporting a dependency absent.
4. **Missing credentials = report and STOP.** Never guess an account, never
   source a `.env` you were not handed. That is the blocking finding, with hit
   count 0.
5. **Report hits against the budget explicitly.** "0 of 2 used because STOP
   fired at preflight" is a *passing* budget outcome, not a failed test — and
   close with one line on whether the risk window cleared or could not be
   assessed.

Two cheap mechanics that decide whether the report is trustworthy:

- **Do not pipe the tool into `tail`/`grep` and then read `$?`** — you get the
  pipe's exit code, not the tool's. Redirect to a log file under the allowed
  artifact dir and capture `$?` immediately (and make sure that dir exists
  first, or the redirect itself fails and you log nothing).
- **An import-time crash costs zero hits and looks like a broken environment.**
  `python -m pkg` dying with `ImportError: attempted relative import beyond top-level package`
  means one module inside a *top-level* package wrote `from ..sibling import x`.
  Diagnose by grepping every relative import and comparing to the majority
  convention (`from .x` vs `from ..x`); the outlier line is the bug. One-character
  fix — report it, don't apply it during a retest.

Preflight replication recipe, the doctor-check enumeration and the retest
report checklist are in
[`references/retest-preflight.md`](references/retest-preflight.md).

## Nx reliability runs: "coba N× test dulu untuk memastikan"

A request to test N times is a request for a **run-level solve rate with an
honest denominator** — not N demos. The harness pattern that survived live
use (two clean 5× batches against Aliyun/Logto → 4/5 and 3/5, **7/10
aggregate**, after the first harness reported a fabricated 3/5):

1. **Loop `N` times; each run is one full chain** (solve → ticket → host
   backend accept). PASS is defined at the *end* of the chain — solver
   `T001` alone is not a pass if the host's `/verify` call never returns
   `{"success":true}`.
2. **Fail closed per run** (see the artifact-freshness pitfall above): unlink
   the outcome file before every run, write per-run stdout to
   `test_Nx_run<i>.log`, and score PASS only from artifacts this run produced.
3. **Health-check the proxy *inside* the run, before burning the attempt:**
   `curl -x $proxy` to ipify + the target (must return 200/302). A dead proxy
   otherwise wastes the run as a confusing 17-second "solver fail".
4. **Retry ladder within a run:** walk up to ~4 candidate proxies in rotation,
   retrying while the solver did not emit success — but only when the failure
   is *retryable*. Mark the run FAIL and move on when the ladder is exhausted.
5. **Classify the failure signature before blaming the chain:**

   | Signature | Time | Meaning | Action |
   |---|---|---|---|
   | `no RESULT`, exit 1, ~17 s | fast | proxy dead or init/transport glitch | retry next proxy |
   | full search sweep, `F015` | ~3–5 min | egress IP bot-flagged (reachability is fine) | retry next proxy; if all candidates `F015`, the pool — not the solver — is burnt |
   | `F015` on *every* proxy incl. direct | — | volume × time keyed risk window | stop the batch, cooldown, reschedule (do not scan more proxies) |
   | host `/verify` `{"success":false}` before drag | — | challenge staleness (layer 2) | shorten decision latency, don't rotate |

6. **Cooldown between runs** (30–60 s) so risk control doesn't throttle the
   batch itself; report the rate as `X/N` **plus** the per-run table
   (run → PASS/FAIL → code → certifyId → egress `host:port`). Never collapse
   it to "all passed" — and never report a first-attempt number when retries
   happened; state how many attempts each PASS took.

Two empirical facts from the 5× batches worth reusing: **gap detection was never
the bottleneck** (every run that reached solve ended in `T001`; the wrong
detector guesses still hit), and **the proxy pool is mixed** — one subnet can
be clean while its neighbors are flagged, so a blacklist should be per-IP and
per-signature, not per-subnet.

### Run the batch twice before quoting a rate

One 5× batch is a small sample; the operator's follow-up (*"jalankan 5x tes
lagi"*) produced a second clean batch that overturned assumptions the first
had earned:

- **Egress risk is volume × time keyed *on top of* IP reputation — blacklist
  with a TTL, never permanently.** `209.50.190.129` passed run 3 and
  full-sweep `F015`ed run 5 of the *same batch*, 16 min apart;
  `209.50.179.43` fast-failed batch 1 then served an 11-second T001 in batch 2;
  `104.207.33.197` (2 PASSes earlier) fast-failed three times later. A
  permanent blacklist empties the pool of IPs that work again within the hour
  — record signature + timestamp and let an IP re-earn its slot.
- **The tail of a batch degrades:** batch 1 lost only its last run, batch 2
  lost its last two, each after 3–4 straight PASSes. Read "N passes then
  consecutive `F015`/init-fails" as the risk window opening, not as pool
  exhaustion: lengthen the cooldown or stop and reschedule rather than
  widening the ladder.
- **Set retry-ladder depth from the pool's health, not a constant.** The fixed
  4-candidate ladder was exhausted twice (both batch-2 FAILs). Walk the whole
  pool when it is small, ordered by last-seen signature (recent fast-fail /
  `F015` last).
- Second-batch run table, same-IP flip timeline and the 7/10 aggregate are in
  [`references/aliyun-inpainting.md`](references/aliyun-inpainting.md).

## Shipping the solver: rebuild it clean, then prove equivalence

A throwaway script that solves has to become a *deliverable tool* at some point.
The risk moves from "does it solve" to two falsifiable questions: **did the
rebuild change behaviour**, and **does it leak secrets**. Both need evidence.

**Split by pipeline stage** — one module each for `config`, `browser`, `network`,
`flows`, `detect`, `decision`, `drag`, `risk`, `engine`, `cli`, so the detector
math can be exercised with no browser and no network. Worked module map, exit
codes and run transcript in
[`references/solver-package.md`](references/solver-package.md); that file also
records the **v1.1.0 patch pass** — the repeatable pattern for folding lessons
learned *after* a hand-off back into the shipped tool (read this skill's
shipping/Nx sections against the existing tree, patch each unencoded lesson,
re-run the zero-hit suite, re-scan, re-zip, re-attach) plus the two
redaction-idempotency bugs only an exact-string unit test caught.
A second rebuild of the same class (jsdom engine + two-step chain, Aliyun)
and its delta — how to fidelity-test a JS engine that doesn't export its
math, the render-timeout/CORS chain failures — are in
[`references/aliyun-solver-package.md`](references/aliyun-solver-package.md).

**Prove fidelity by running old and new in one process on identical inputs** —
never by diffing against a stored artifact:

- Stored artifacts are an invalid oracle: the capture file is rewritten at the
  *end* of a run with the final prehandle, while `bg/fg` belong to the last
  *detected* round. Diffing new output against the file on disk reported five
  bogus mismatches, every one traceable to a different `init_pos`.
- Import both implementations, feed the same in-memory bytes, diff
  `json.dumps(x, sort_keys=True)` over the same key set.
- Cover three passes: (1) real inputs, (2) **forced consensus** — patch the
  detector outputs to a single x so the non-empty drag-candidate path is
  actually exercised (the stored pair legitimately yields "no cluster"), and
  (3) the decision state both builders emit.
- Compare the two implementations' *own* outputs, not fields the test derives
  itself: an out-of-date expression in the test reported a mismatch the product
  code did not have, and cost a real debugging round.

**Credential-free is a hard requirement, not a nicety** (operator: *"pastikan
tools tersebut bersih dari kredensial"*):

- Secrets arrive only via env/CLI (`TCAPTCHA_EMAIL`, `--proxy-file`, …) — a
  `tcaptcha env`-style subcommand prints an **empty** template; a legacy env
  fallback (`TCAPTCHA_*` → `JEV_*`) is acceptable if documented, and a missing
  variable is never permission to guess one.
- Redact at the logging layer: `scheme://user:pass@host` → `***:***@host` in
  every line, outcomes carry `direct` / `host:port` only. Cover **both proxy
  forms**: a bare `user:pass@host:port` has no scheme, so `urlsplit()` reads
  `user` as the scheme, `hostname` comes back `None`, and urlsplit-only
  redaction hands the credentials back **untouched** — and the logger must not
  gate scrubbing on `"://" in text`, or a bare proxy inside a message is never
  touched at all.
- **Unit-test redaction with exact expected strings, including re-redaction.**
  `redact(redact(x)) == redact(x)` on both forms plus one exact scrub assertion
  caught two bugs that every functional test passed: the bare rule's user class
  allowed `*`, so an already-redacted `scheme://***:***@host` re-matched from
  the second star and grew one star per pass (`http://****:***@…`) — exclude
  `*` (along with `/` and `:`) from the user class; and the scheme scrub
  appended `***:***` then jumped past `@`, emitting
  `http://***:***1.2.3.4:3129` — append `***:***@` and jump *to* the `@`. A
  redactor that is not idempotent corrupts or leaks on the second pass, and
  only exact expected strings surface that.
- **Prove it with a pattern scan over the tree** — e-mail domains, known password
  literals, `user:pass@ip`, `password = "..."` assignments, literal env
  assignments — and report `0 hits` **in the tree and inside the built zip**
  (unpack to a temp dir; the archive is the artifact, not the tree). A
  grep-clean claim is checkable; "no credentials included" is not. Names of
  env vars in a documented fallback are not hits; *values* are.

**Give the caller an honest exit contract**: `0` solved, `3` attempts exhausted,
`4` risk hold (placeholder — nothing solvable, stop without burning attempts),
`2` config/transport error (e.g. every proxy candidate failed its health check —
fail closed *before* browser spawn with `no healthy proxy candidate`, never a
misleading `IFRAME NOT FOUND`); `doctor` non-zero when NOT READY. Read `$?`
un-piped, or you are measuring `tail`. Ship a redacted `run.log` sink with the
tool: a failure whose only trace was one stdout line is undiagnosable after the
process exits.

**The live e2e will fail at the chain, not the solver — budget for it.** In
rebuild #2 the solver minted `T001` and the run still died twice before
EXIT=0; both failures were contract bugs the offline suite cannot see:

- **Render timeout only under the ladder.** The engine solved when spawned by
  hand but returned `slider render timeout (15s)` on 6/6 orchestrated
  attempts — the ladder's `curl -x` health check left the single-session proxy's
  connection churned, and the next CONNECT was dropped. Fix: sleep ~4 s
  between health check and spawn, and persist the engine's **full stdout** to
  `work/attempt_N/engine.log` (redacted); one stripped error line made this
  undiagnosable until the transcript existed. A verification subcommand that
  passes in isolation and fails inside the run's own sequence is the signature.
- **Host verify must be same-origin.** An absolute `--verify-url` on a
  different subdomain than the live auth page throws
  `page.evaluate: TypeError: Failed to fetch` *after* T001 — CORS, not captcha
  failure. Submit the path relative to `page.url`'s origin with
  `credentials:'include'`; that is what the trials always did.

Build-time traps hit while generating the tree itself: source written inside a
script with a non-raw `'''` body turns `f"\n…"` into a real newline in the
file (write shipped source with `r'''…'''` or the dedicated write tool);
`doctor --fix` must provision *before* it checks or it reports FAIL for what it
just fixed; exclude zip artifacts by explicit filename, never `-x '*.json'`
(it eats `package.json` and ships a tool with nothing to `npm install`);
`compileall` is green on a `NameError`, so smoke-import every module — and run
that smoke test from a **fresh unpack of the zip you are about to attach**, not
from the repo tree (a zip can pass in-place and fail on a clean machine). Full
transcript: [`references/aliyun-solver-package.md`](references/aliyun-solver-package.md).

**Provision every runtime the tool depends on — do not assume it exists.**
Immediately after the zip was handed over, the operator asked *"itu source dah
termasuk minta browser apa kan?"* and the honest answer was **no**: the tool
worked only because Chromium already sat in `~/.cache/ms-playwright`. A
deliverable that runs on your box and dies on a clean one is not shipped. Close
the gap:

- One `ensure_chromium(pw)` helper: when `pw.chromium.executable_path` does not
  exist, run `subprocess.run([sys.executable, "-m", "playwright", "install",
  "chromium"], capture_output=True, timeout=900)` and re-check. On failure raise
  `RuntimeError(f"chromium still missing at {exe}: {tail}")` so the message
  carries both the expected path and the installer's own output.
- Call it from **both** call sites — `Session.__enter__` (first solve) and a
  labelled `doctor` `browser` check — so the code that provisions is the code
  that verifies. `doctor` then prints `OK browser: <exe path>`.
- **Test the cold path, not the warm one.** `PLAYWRIGHT_BROWSERS_PATH=<empty
  dir>` + one call → measured 48 s / 658 MB → executable returned; the
  already-installed branch and a guaranteed-failure branch (fake path →
  `RuntimeError`) were checked separately. Running only the warm branch proves
  nothing about provisioning.
- Declare in the README that the browser is the *only* thing the tool fetches
  on its own; credentials, proxies and the four captcha assets still come from
  the environment / runtime.

Then run the same audit over the remaining hard dependencies: external CLIs
(`jev-decide` → the arbiter check), the interpreter that carries the deps
(print its absolute path in the README run line), and input files
(`--proxy-file`). Rule of thumb: **every dependency is either provisioned on
demand or is a labelled `doctor` check** — a silent assumption is a crash
waiting for someone else's machine, and the operator's one-line question is the
cheapest review you will get.

**Hand-off means the artifact reaches the chat.** *"lampirkan sourcenya kesini"*
= attach with `MEDIA:/absolute/path` plus a file map and size (`30 KB, 16
files`) and the exact `unzip` → run line; naming a filesystem path delivers
nothing to a chat client. Zip is the right shape for a tool hand-off (reports
stay plain files), and **any patch after the hand-off makes the attached
archive stale** — `ensure_chromium()` landed *after* the first zip was sent, so
the archive was rebuilt and re-sent as "the updated one" rather than left to
drift. Re-scan the tree for secrets immediately before zipping and ship the
`0 hits` line with the file.

## Where a decision model helps (and where it does not)

Give a typed-decision model the *numeric evidence and the candidate actions*,
not the image. It arbitrates; it does not perceive.

- **Proven blind to pixels.** An image reading `ZEBRA`, asked "does this image
  contain ZEBRA?", answered `0.09`; the same image reading `HIPPO` answered
  `0.12`. Image tokens were consumed (~6125) but ignored. Do not send images
  expecting perception — encode detector scores, agreement flags, cluster
  positions as text.
- **Real value is the reject path.** Given contradictory evidence it chose
  `refresh` (`needs_review`, p=0.44) instead of guessing between candidates;
  given 2/3 consensus it chose the drag (p=1.0). Always offer `refresh`/`abort`
  labels alongside the drag candidates, and say in the instructions that weak
  evidence should prefer them.
- **Choice labels must map to executable host actions.** Never ask it for raw
  coordinates — ask which of *your* candidates to run.
- Exit code `2` means "needs review", not an error (`1` is error, `0` selected).

Full worked request shape and the TCaptcha protocol specifics are in
[`references/tcaptcha.md`](references/tcaptcha.md).

## Aliyun INPAINTING (Captcha 2.0 V3): three failure layers that hide each other

The same "detect gap → drag → verify" shape on Alibaba's `initAliyunCaptcha`
widget (INPAINTING type: AIGC-blurred slot, 300×300 bg + W×300 RGBA strip)
failed repeatedly for **three unrelated reasons**, and each layer's symptom
points at the next one:

| Layer | Symptom | Root cause | Fix |
|---|---|---|---|
| 1. drag mapping | landing err +25…+160px even with a "calibrated" linear probe | displacement → puzzle position is **quadratic**: `puzzle = 0.00355·d² + 0.077·d − 0.005` (measured 25→4.14, 97→40.1 — matches to <0.9px) | invert the quadratic for the target displacement, then **closed-loop correct against live `puzzle.style.left` before mouseup** — verify fires on release, so you get one shot |
| 2. staleness | host `POST .../captcha/verify` returns `{"success":false}` *before* your mouseup; Aliyun verify never sent | host session (here Logto) times out ~120 s after challenge capture while the decision pipeline runs | stamp the capture; if age > 120 s, refresh for a fresh challenge BEFORE dragging; keep decision latency inside the window |
| 3. risk flag | every attempt returns `F015` *even at the right coordinate*, in-browser and in jsdom alike | `F014` = wrong position, **`F015` = bot/IP flag**; datacenter egress is flagged regardless of accuracy | rotate egress to a non-datacenter proxy; gap accuracy is not the bottleneck |

Full evidence, the winning end-to-end chain (jsdom protocol solver + proxy →
`captchaVerifyParam` → `POST /api/experience/captcha/verify {captchaToken}` →
`{"success":true}`), the in-page `__allFetch` tracing recipe and OSS repo
evaluations are in
[`references/aliyun-inpainting.md`](references/aliyun-inpainting.md).
The trial scripts were later rebuilt into the shippable **Aliyun Solver** tool
(`solve · doctor · detect · env`, credential-free, exit contract
`0/1/3/4`) — module map, fidelity proof and e2e failures in
[`references/aliyun-solver-package.md`](references/aliyun-solver-package.md).

### Pitfalls worth not re-deriving (Aliyun)

- **A linear calibration probe on a quadratic widget lies.** Probe 25 px →
  puzzle shifted 4.14 px (ratio 0.166); the sanity clamp fell back to `1.0` and
  *every* drag undershot. Read the mapping from two points (or the widget's
  published formula) before dividing, and never add the probe displacement
  twice (`total = move + probe` landed 25 px right of an already-correct target).
- **Hook `initAliyunCaptcha` on assignment, not on a polling interval.** A 50 ms
  `setInterval` wrapper raced the SDK's immediate invocation and lost the config
  (`scene: null`); `Object.defineProperty(window, 'initAliyunCaptcha', {set(fn){...}})`
  installed from `add_init_script` catches every call.
- **`F015` reads exactly like a wrong answer.** 31 attempts, verify visibly
  triggered, all `F015` — the coordinate was never the problem. Classify the
  code *before* re-tuning detection: `F014`→measure, `F015`→change egress.
- **Playwright proxy credentials must be split.** Chromium ignores
  `user:pass@host` inside `proxy.server` → `407 Proxy Authentication Required`
  (looks like the proxy is dead). Parse userinfo into separate
  `username`/`password` fields.
- **CSP `connect-src` on the host page can block the SDK's own asset fetches**
  (e.g. `static-captcha.aliyuncs.com` not allowlisted) — the widget still
  renders from cache but the console fills with violations. Read CSP errors as
  environment evidence, not as the cause of a wrong answer.

### Keep the decision pipeline out of the agent's turn latency

When the challenge window (~2 min) is shorter than one agent turn, a
tool-call-per-detector decision loop misses every window — observed four
consecutive "stale, refreshing" cycles at 211–413 s age. Architecture that
fixed it: after capture the engine **spawns a child process** that runs the
detectors → typed decision (gap choice) → action decision → writes a decision
file the engine polls, giving the agent an 8 s head start for a manual
override. Capture → decision dropped from 5–12 minutes to **16 s**. The agent's
job moves to reviewing outcomes and patching the engine, not feeding it
decisions one turn at a time.

## Portability: what carries to a new site and what does not

When asked whether a shipped solver is *"universal, selama web itu pakai
TCaptcha"*, answer in three buckets — then prove it by reading the tree, not
by asserting:

- **Universal (widget-level, zero edits):** `network` (the endpoints
  `cap_union_prehandle` / `getcapbysig` / `cap_union_new_verify` are Tencent's
  *global* widget API — real URLs are captured live, so per-site `aid`/`domain`
  params take care of themselves), `detect`, `decision`, `drag`, `risk`,
  `engine`, and `wait_for_widget` (frame resolved by `captcha`/`gtimg` URL
  substring, which matches nearly every integration).
- **Per-site (exactly one file):** the *flow adapter* — the code that walks the
  host page to the point where it requests a captcha. A registry
  (`@register("name")`, run via `--flow name`) keeps this to ~15 lines per site:
  navigate → fill/submit the trigger → `return wait_for_widget(page, cap, ...)`.
  Contract: *reach the point where the host page requests a captcha, return the
  widget frame once prehandle has answered.* Skeleton in
  [`references/portability.md`](references/portability.md).
- **Hard limits — say them up front:** (1) only the **slider** challenge type is
  solved; a host using TCaptcha's click/rotate variants needs a new
  detect+action; (2) the prefetched assets are **hash-pinned** to one widget
  version (`tcaptcha-frame.<hash>.js`) — another host on a newer widget misses
  the cache and degrades to pass-through fetch (fine unless the proxy drops
  CONNECT; then auto-discover the script `src` from the page instead of
  hardcoding); (3) non-TCaptcha (reCAPTCHA/hCaptcha/Turnstile) is a different
  problem entirely — this tool does not apply.

Workflow note: to find every skill covering a capability, grep the skill tree
itself (`grep -rilE 'captcha|turnstile|geetest' skills-dir --include=SKILL.md`)
rather than trusting recall — it surfaced `security/captcha-solving`,
`security/slider-captcha-solving` and `hunt-captcha-bypass` (plus incidental
mentions) in one pass.

## Complementary skills

- **`hunt-captcha-bypass`** — for *removing* the gate: omitted field, token
  replay, client-side-only validation, per-endpoint gaps. Start there when you
  suspect the server never checks. Reach for this skill when the server does
  check and a real challenge must be satisfied.
- **`security/captcha-solving`** — broader vendor coverage (NetEase Yidun,
  GeeTest, Alibaba NoCaptcha), pure-HTTP protocol, appid extraction, and
  `references/solver-repos.md` (a field-tested comparison of three OSS solver
  repos: `crackTCaptcha`, `CaptchaCracker`, `tencent_crack_captcha`). Overlaps
  this skill on TCaptcha gap detection — if only one survives consolidation,
  keep this one's empirical pitfalls and that one's repo survey.
