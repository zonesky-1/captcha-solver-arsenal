---
name: captcha-solving
description: "Solve vendor CAPTCHA challenges on authorized targets when field-omission and replay bypasses are unavailable. Use for slider/icon-click/word-click/image-select challenges (Tencent TCaptcha, NetEase Yidun, GeeTest, Alibaba NoCaptcha) and for deciding which class of CAPTCHA is even solvable offline. Covers appid extraction, pure-HTTP challenge protocol, gap detection with cross-validation, behavioural payload generation, and honest verify-stage triage. Complements hunt-captcha-bypass (which covers validation flaws); this skill covers producing a genuine solution."
---

# Solving vendor CAPTCHAs

## Where this sits relative to `hunt-captcha-bypass`

Always exhaust **bypass** first — it is far cheaper and higher signal:

1. Omit the CAPTCHA field entirely (client-side-only validation).
2. Empty / null / `0` value.
3. Replay a solved token (no single-use enforcement).
4. Same token on a different endpoint (no action binding).
5. CAPTCHA only enforced after N failures.
6. Audio / accessibility channel.

Only when all six fail does **solving** apply. Do not silently escalate into
solving when a bypass test is still outstanding — report which bypasses you
tested and what they returned.

## Triage: which CAPTCHAs are worth solving offline

| Class | Offline-solvable? | Approach |
|---|---|---|
| Google reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile | No (realistically) | Needs external solving service or a live browser session. Budget-gated; say so and stop. |
| Vendor slider / icon-click / word-click with a documented HTTP protocol (TCaptcha, Yidun, GeeTest, NoCaptcha) | **Yes** | Pure HTTP: `prehandle → fetch challenge image → solve → verify`. No browser needed. |
| Custom in-house image/math CAPTCHA | Yes | OCR / template match; often easier than anything below. |

The vendor class is the interesting one: the challenge is just three HTTP
round-trips plus an image-processing step. Open-source solvers exist for it —
see `references/solver-repos.md` for what each one actually covers.

## Workflow

### 1. Extract the appid / site key

CAPTCHA appids are almost never in the static JS bundles — the widget is
lazy-loaded, so `grep` over downloaded chunks comes up empty. Extract it at
runtime instead:

- Hook the constructor **before** triggering the challenge, then trigger it and
  read the captured argument. For TCaptcha: wrap `window.TencentCaptcha`,
  log the first argument, then complete the form step that raises the challenge.
- Alternatively hook `fetch`/`XMLHttpRequest` and read the `appid`/`aid` query
  param off the `prehandle` request.
- Confirmed working: constructor hook captures the appid on the first trigger.

Do not bother downloading and grepping the whole JS bundle first — it wastes a
round-trip and normally returns nothing.

### 2. Speak the challenge protocol directly

TCaptcha shape (others are similar, different paths):

- `cap_union_prehandle` → JSONP wrapped as `_aq_XXXXXX({...})`; strip the wrapper
  before `json.loads`. Carries `sess`, `pow_cfg` (PoW prefix + target md5),
  `dyn_show_info` (element list, background config, `tdc_path`).
- `cap_union_new_getcapbysig?img_index=1` → background; `img_index=0` → foreground
  sprite sheet (piece + slider handle + decoy bar, laid out by `sprite_pos`).
- `cap_union_new_verify` → POST `ans`, `sess`, `pow_answer`, `pow_calc_time`,
  `collect`, `tlg`, `eks`.

TLS fingerprint matters: plain `urllib`/`requests` get 403. Use a client with
Chrome TLS/HTTP2 impersonation, or a real browser session for capture.

### 3. Detect the gap — and cross-validate it

Run **two independent** detectors and require agreement within a few pixels:

- **Alpha-masked RGB/NCC** template match of the piece against the background.
- **Canny edge × piece contour** cross-correlation (`cv2.Canny` on the blurred
  background, `findContours`+`drawContours` on the piece alpha mask, then
  `cv2.matchTemplate`). Fast and robust against texture.

Agreement is the confidence signal. A single method matching cleanly is *not*
proof — see the decoy pitfall below.

**Caveat added from live trials:** these two are *not* actually independent —
both ask "which place in the background looks like this piece?", so they can
lock onto the same decoy and agree on the wrong coordinate. Consensus narrows
risk without removing it, so keep a reject-and-refetch path in the loop and
never drop the failure branch once agreement looks good.

**Evidence note (corrected):** the early rows used to support this — `339/339`,
`459/460`, `411/411/413` — all came back `errorCode 12`, which is now known to
be Tencent's **IP rate-limit / risk-control** code, not a refused coordinate
(citations and a reproducible batch in
`slider-captcha-solving/references/rate-limit-and-risk-control.md`). They are
retracted as proof of consensus failure. The real evidence for the decoy claim
is the visual/numeric measurement table (outline-match strength ranks the decoy
`45.3 M` above the true hole `23.6–31.5 M`, interior/ring `0.54` vs `1.07`) in
`slider-captcha-solving/references/consistency-and-retry.md`, along with the
interior-ratio override that lost 0/5. Use those, not the `12` rows — and triage
`12` as a rate limit before re-tuning any matcher.

### 4. Generate the behavioural payload

- Answer coordinate space: confirm whether the server wants image pixels or the
  logical canvas (declared `size_2d` often differs from the downloaded PNG size).
  Derive it from `init_pos` / `track_limit`, never from assumption.
- Drag trajectory: ease-in-out cubic from `init_pos`, ±1 px jitter. Perfectly
  smooth trajectories get flagged.
- PoW: `md5(prefix + nonce) == target_md5`, but **report** a realistic
  `pow_calc_time` (300–500 ms) — Python hashes far faster than a browser and a
  sub-200 ms claim is itself a detection signal.
- Behavioural blob (`collect`/`eks`) comes from running the vendor's own
  collector JS in a DOM shim, not from hand-faking it.

### 5. Triage the verify stage honestly

Capture ground truth before guessing: let the real widget solve once under
Playwright with `page.on("response")` hooked on the verify endpoint, then
diff your payload field-by-field. See `references/tcaptcha-protocol.md` for the
exact capture recipe and the failure modes already characterised.

Report the true `errorCode`. Distinguish clearly between:

- **Solved** — `verify` returns success + a ticket.
- **Detection is right, verify rejects** — gap confirmed visually/pixel-wise,
  but the behavioural payload is wrong or the request is flagged. The fault is
  *not* the matcher; stop re-tuning it.
- **Unsolvable offline** — say which class and why, offer the bypass results.

If the verify stage stays unresolved after a payload diff, stop and report
honestly rather than looping on the matcher. Ask for an alternative solver
implementation at that point instead of re-deriving one.

## Pitfalls

- **The element list is not ordered by role.** The movable piece is not
  element[0]. Static decoys ship alongside it — a caption bar and the slider
  handle itself. Select the element whose raw `move_cfg.data_type` contains
  `DynAnswerType_POS`.
- **Backgrounds draw a decoy silhouette** at the piece's *start* position. A
  matcher that locks onto it returns a confident, wrong answer. This is the
  single most common false positive, and why two detectors must be run.
- **Coordinate-space mismatch.** Declared background `size_2d` and the actual
  downloaded PNG dimensions frequently disagree (e.g. logical 672×480 with the
  slider living outside the 672×390 image). Using the wrong space silently
  produces an out-of-range answer.
- **Schema drift in solvers.** Open-source solvers are written against the
  protocol as it looked when authored. Expect key-shape mismatches in parsed
  responses (`elem_id` vs `id`, `{"x":..,"y":..}` vs `[x, y]`). Patch the
  parser rather than assuming the solver is unusable.
- **Referer/Origin are cross-checked** against the captcha registration on the
  verify request. Pass the real entry URL through all three phases.
- **Playwright on client-side-rendered auth pages:** wait on the selector with a
  long timeout, not a fixed sleep. Custom ToS checkboxes expose no
  `input[type=checkbox]` — click the agreement text instead.

## Boundaries

Authorized targets only. Do not run a solver at volume against third-party
services, and do not use it for account farming, credential stuffing, scraping
at scale, or any purpose outside the operator's stated scope. A single
validation solve to establish whether the protection holds is in scope;
sustained automation is not.

## Support files

- `references/tcaptcha-protocol.md` — TCaptcha field-level notes, the verified
  appid extraction, response schema quirks, verify failure modes, ground-truth
  capture recipe, and the currently open questions.
- `references/solver-repos.md` — comparison of three open-source solver
  repos: what each covers, its language/runtime, and how much patching it needs.
- `scripts/gap_crosscheck.py` — deterministic cross-check of the two gap
  detectors; run it on a downloaded background/sprite pair before trusting
  either answer.
