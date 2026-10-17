# Aliyun Captcha 2.0 V3 (INPAINTING) — worked engagement notes

Target: `auth.atria-asi.ai` (Logto-hosted flow, `SceneId=1up68vza`,
`prefix=17qfjo`, mode `embed`), challenge type `INPAINTING`. Outcome after
fixing three independent layers: **T001 three runs in a row** and the final
backend call accepted the token:

```
POST https://auth.atria-asi.ai/api/experience/captcha/verify
body {"captchaToken": "<captchaVerifyParam>"}
-> 200 {"success":true}
```

## The three layers, with the evidence that identified each

### 1. Drag mapping is quadratic, not linear

Live instrumentation (`probe:` / `mouseup:` logs reading
`#aliyunCaptcha-puzzle.style.left` during a held drag):

| mouse displacement | puzzle shift | formula `0.00355·d² + 0.077·d − 0.005` |
|---|---|---|
| 25 px | 4.142 px | 4.14 |
| 97 px | 40.104 px | 40.9 |

A linear probe (25 px → 4.14) gives ratio 0.166, which fell below the sanity
clamp (`0.2 <= ratio <= 6`) and silently used `1.0` — every subsequent drag
undershot by ~6×. Inverting the quadratic:

```
S = (−b + sqrt(b² − 4a(c − P))) / (2a),  a=0.00355 b=0.077 c=−0.005
```

plus a closed-loop correction before mouseup (re-read `puzzle.style.left`,
compute `dd = err / (2a·d + b)`, nudge) produced landing error
**+0.1 to +0.7 px** on every attempt thereafter. Note verify fires *on
release* — the whole correction must happen while the button is held; you get
one mouseup per challenge.

The same coefficients are published by two independent OSS repos (see below),
so the mapping is widget-global, not per-site.

### 2. The host session expires while you are thinking

Symptom: `POST /api/experience/captcha/verify → {"success":false}` arriving
*before* mouseup (request logged 15:16:54, mouseup 15:16:55), Aliyun's own
verify never sent, widget immediately re-inits a new challenge. Four
consecutive attempts failed this way with decision ages of 211–413 s.

Fix: stamp `captured_at` in the round metadata; before dragging, if
`age > 120 s`, refresh the challenge and demand a *new* decision on the new
images. Combined with layer 3 (below), the practical rule is: **decision
latency must fit inside the challenge lifetime, not just be correct.**

### 3. `F015` is an IP/fingerprint flag, not a coordinate

jsdom protocol solver, 31 drag attempts, `verify=触发` on many of them:

```
drag=147 → F015 ... drag=180 → T001
```

`F014` = wrong position; `F015` = bot-detected. The capsolver repo README
states it outright: *"image detection 76/76 correct … F015 persists on
Hetzner datacenter IP — needs residential proxy for F000."* Our egress was AWS
`54.227.x`. One proxy hop (a pool entry on a different subnet) and T001
followed within the same search sweep. So:

- gap accuracy was **never** the bottleneck — the winning runs even had a
  *wrong* detector guess (`gap x=45`, `x=260` vs actual drag 176/182) and the
  ±15 search around a sane default still hit;
- spend effort on egress and drag integrity, not on a fifth detector.

## Winning chain (reproducible, 3/3)

```
1. node captcha-solver/solve-captcha.js        # jsdom protocol solver
   env: JSDOM_PROXY=<http proxy>  CAPTCHA_PREFIX=17qfjo
        CAPTCHA_SCENE_ID=1up68vza  CAPTCHA_PAGE_URL=https://auth.atria-asi.ai/sign-in
   - detectGap patched -> local capsolver detector (no vision API needed)
   - hpagent swaps http/https globalAgent so InitCaptchaV3/VerifyCaptchaV3 egress via proxy
   -> T001 + securityToken + certifyId + captchaVerifyParam
2. Browser session: drive to the Logto step that owns the captcha, then
   POST {captchaToken: param} to /api/experience/captcha/verify
   (contract read from the app bundle: y5 = t => q.post(..., {json:{captchaToken:t}}))
-> {"success":true}
```

Injection alternative: capture `initAliyunCaptcha`'s `cfg` via
`Object.defineProperty(window,'initAliyunCaptcha',{set(fn){...}})` and call
`cfg.success(param)` — works (`cvp_set=True`) but the direct backend POST is
what produced the verified `success:true`.

## 5× reliability test (2026-10-02) — 4/5, harness bugs first

Operator: *"coba 5x test dulu untuk memastikan"*. Two batches were needed
because the first harness lied.

**Batch 1 (broken harness): reported 3/5, honest recount 1/5.** Two bugs:

1. **Stale-artifact false-PASS.** The outcome file was deleted once at batch
   start, so runs 4 (`no RESULT`, 17 s — dead proxy) and 5 (`F015`, full
   sweep) were scored from run 3's leftover `final_result.json`. Fix: `unlink`
   before *every* run + require the win string in this run's own stdout.
2. **No proxy health-check / retry.** A dead proxy consumed the whole run.
   Fix: `curl -x` ipify + target before each attempt, then a retry ladder of
   up to 4 rotating candidates per run.

**Batch 2 (clean harness): 4/5 PASS**, each PASS = solver `T001` **and**
host `POST /api/experience/captcha/verify` → `{"success":true}`:

| Run | Result | certifyId | Egress (host:port) | Attempts |
|---|---|---|---|---|
| 1 | PASS | `EeyhEJRgr6` | 104.207.33.197:3129 | 3 (2 fast fails) |
| 2 | PASS | `orj6wEgZKj` | 104.207.33.197:3129 | 2 |
| 3 | PASS | `Xv9jMBYwDo` | 217.181.92.77:3129 | 2 |
| 4 | PASS | `y45UzX0iLT` | 209.50.190.129:3129 | 3 |
| 5 | FAIL `F015` | — | 4 candidates exhausted | 4 |

Failure signatures observed across both batches:

- `no RESULT` + exit 1 in ~17 s → proxy dead / init glitch. Retryable.
- full sweep + `F015` in ~3–5 min → egress IP flagged (health-check still
  passes — reachability ≠ clean reputation). Retryable per-IP.
- detector/gap never caused a failure: every run that reached solve ended
  `T001` (drag 180–205), including runs with wrong detector guesses.

Pool facts: mixed quality within one `/16` — `209.50.190.129` clean while
`209.50.179.43`, `209.50.170.8`, `209.50.187.27` flagged/fast-fail. Blacklist
per IP + signature, never per subnet. Run script: `/root/atria_solve/test_5x.py`,
per-run logs `test_5x_run<N>.log`, summary `test_5x_summary.json`.

**Batch 3 (operator: *"jalankan 5x tes lagi"* — same clean harness): 3/5 PASS.**

| Run | Result | certifyId | Egress | Ladder |
|---|---|---|---|---|
| 1 | PASS | `rgEvbOPzmO` | 209.50.179.43:3129 | 2nd candidate, solve in 11 s |
| 2 | PASS | `EVoYdlzePG` | 217.181.92.77:3129 | 3rd candidate |
| 3 | PASS | `m6lyJXuhVE` | 209.50.190.129:3129 | 4th candidate, drag=231 |
| 4 | FAIL `F015` | — | 4 candidates exhausted | last attempt full sweep |
| 5 | FAIL `no RESULT` | — | 4 candidates exhausted | 1 full sweep + 3 init-fails |

**Aggregate over the two clean batches: 7/10 (70%) at run level — and 7/7 for
the solve phase**: every attempt that entered solve on an unflagged egress
ended `T001` → host `{"success":true}`. All 3 run-level FAILs were egress
(ladder exhausted on fast init-fails / `F015` sweeps); gap detection caused 0.

Evidence batch 3 added beyond batch 2:

- **Same-IP flips inside one batch.** `209.50.190.129`: PASS at 17:53 → 18 s
  init-fail at 17:59 → full-sweep `F015` at 18:09. `217.181.92.77`: PASS at
  17:46 → full-sweep fail at 17:58. IP reputation alone does not predict the
  next attempt — cumulative attempt volume over the batch is a co-factor
  (same conclusion as the TCaptcha placeholder matrix), so a permanent
  per-IP blacklist is wrong in both directions.
- **Tail degradation repeated:** batch 2 lost run 5 only; batch 3 lost runs
  4–5, both after 3–4 straight passes. Two-for-two pattern → treat late-batch
  consecutive failures as a risk window opening (lengthen cooldown / stop),
  not as pool exhaustion.
- **Fixed ladder depth is the wrong abstraction.** Both batch-3 FAILs are
  "4 candidates burned"; pre-rank the pool (recent fast-fail / `F015` last)
  and walk past 4 when the pool is small, or at least one of them had a
  candidate left to try.

## In-page request tracing (`__allFetch`)

To see what the SDK actually sends when Playwright's `response` log is not
enough, install from `add_init_script`: wrap `window.fetch` and
`XMLHttpRequest.prototype.open/send`, push `{u,k,m,b}` (req/res/err) into
`window.__allFetch`, dump the tail after each observe. This is how we proved
the browser-path SDK **never issued VerifyCaptchaV3** (only `InitCaptchaV3`,
`UploadLog`, `Log1-3`) while the jsdom path did — the browser path was failing
silently at layer 2/3, not at detection.

## Decision pipeline architecture (latency fix)

`auto_decide.py`, spawned by the engine 8 s after capture (agent keeps a manual
override window):

```
detectors (capsolver + RGB/edge/smoothness ensemble + exact-width transition-pair scan)
  -> jev-decide gap_x (choice over the candidate set)
  -> jev-decide action  (drag / refresh / abort)
  -> write gap_roundN.json   # engine polls this
```

Measured capture → decision: **16 s** (vs 5–12 min doing the same via agent
turns). Pairs with the typed-decision guidance in the main SKILL.md: candidates
as labels, `refresh`/`abort` always offered, `needs_review` honored.

## OSS repos evaluated this session

| Repo | What it is | Verdict |
|---|---|---|
| `xaoLiu1222/aliyun-captcha-v3-inpainting-solver` | jsdom protocol solver: env patches, `isTrusted` (must re-patch *after* SDK load), quadratic inverse, human drag, search ±15 | **The engine that produced all 3 T001s** after patching detectGap → capsolver and adding hpagent proxy |
| `Sansa-Organisation/capsolver` | Self-hosted FastAPI solver for all Aliyun V3 types; `solver.py::detect_gap` (mvar/depth/boundary-ratio/gabor, scene classification) + `drag.py` with the same quadratic | Best INPAINTING detector found (conf 0.89–0.99 on live challenges); usable as a plain library (`solve_gap_from_files`) |
| `B00H0O/Aliyun-Solver` | Minimal `solver.js` + bundled `AliyunCaptcha.js` | Thin; no protocol layer worth lifting |
| `chiaweilee/vue-nocaptcha` | Vue 2 wrapper around old (2018) `nc.js` NoCaptcha SDK | Integration reference only, not a solver |
| `Liuziyu77/Valen` | Multimodal decision model (Jev-like: candidates → probabilities) | Qwen3.5-2B backbone needs GPU + ~2 weights; HF Space demo blocked by ZeroGPU quota. Watch, don't depend on it |
| `IamBusy/OpenJev-Vision` | Open Jev clone; text `OpenJev-0.6B` runs offline (`uv sync --extra qwen`, ~1.2 GB, hash-verified) | Working **local** typed-decision fallback when no API key/quota: `openjev_gap.py` wrapper, ~15 s/call |

## Environment quirks hit (fixes, not rules)

- Playwright proxy: split `user:pass@host` into `username`/`password` fields
  (else 407).
- `page.request.get` to `static-captcha.aliyuncs.com` intermittently died with
  TLS `socket disconnected` — retried 3× then fell back to an in-page
  `fetch → base64` (which the host CSP may then block; read the console).
- Host CSP `connect-src` allowlists `captcha-open.aliyuncs.com` but not
  `static-captcha.aliyuncs.com`; violations are noise for <img>-loaded assets.
