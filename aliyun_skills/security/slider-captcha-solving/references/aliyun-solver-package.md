# Aliyun Solver — rebuild #2 of the solver-tool class (session detail)

Same operator shape as `solver-package.md` (*"bangun ulang tools source code …
pastikan bersih dari kredensial"*) but against the Aliyun/Logto trial
knowledge, so this file is the **delta** vs the tcaptcha rebuild: what a
jsdom-engine port and a two-step chain (solver + host verify) add.

## Result

`/root/aliyun-solver` — run with the interpreter that carries the deps:

```bash
cd /root/aliyun-solver
/root/crackTCaptcha/.venv/bin/python -m aliyun_solver <solve|doctor|detect|env>
```

| module | responsibility |
|---|---|
| `config.py` | CLI > env > defaults; site config has no defaults (never guessed); `proxy_label()` = host:port only |
| `log.py` | `redact()` on every line: `scheme://user:pass@host` → `scheme://***@host`, bare `user:pass@ip` → `***:***@ip` |
| `drag.py` | quadratic MAP + inverse + `build_search_sequence` (Python mirror of the JS engine) |
| `detect.py` | capsolver backend via `CAPSOLVER_PATH` (optional), built-in fallback, CLI contract `GAP=<int>` |
| `risk.py` | failure-signature classification (F015 → rotate egress, NO_RESULT → transport, F014 → coordinate) |
| `engine.py` | provisioning (`npm install`, fetch-sdk), health check, retry ladder, fail-closed artifacts |
| `chain.py` | optional Playwright host verify — **relative-path** same-origin POST (see below) |
| `cli.py` | `solve · doctor · detect · env`; exit contract below |
| `engine/solve.js` | port of the trial jsdom engine: AI-vision path, panel/visual, hardcoded paths removed; drag math byte-identical |

Vendor SDK is NOT shipped: `engine/fetch-sdk.js` downloads `AliyunCaptcha.js` +
`feilin134.js` from the official CDN on demand (md5-verified against the trial
copies). Node deps (`jsdom`, `hpagent`) come from `doctor --fix` → `npm install`.

## Fidelity proof for a JS engine that doesn't export its math

The trial engine exports only `{solve, __setPanel}`; `solveDragDistance` and
`buildSearchSequence` are module-private. Technique:

1. Read the reference source, append `module.exports = {solveDragDistance,
   buildSearchSequence}` in a **temp copy next to the original** (so its
   `require` paths still resolve).
2. `js_eval(file, expr)` → fresh `node -e` per side, same expressions, stdout
   compared in Python. Never one process with both engines loaded.
3. Grid, not spot checks: all 256 `gapX` inputs 0–255 → **256/256 agree**;
   `buildSearchSequence` over in-range/out-of-range/edge centers → **9/9 agree**.
4. Detector diff in the same run: stored live `bg`/`puzzle` pair → reference
   `capsolver_gap.py` `x=56` vs new `detect.py` `x=56 conf=0.99 method=capsolver`
   (needs `CAPSOLVER_PATH=/root/capsolver_sansa/src` and
   `ALIYUN_SOLVER_PYTHON=<venv>` — the detector imports cv2).

`FIDELITY: PASS (3 passed, 0 skipped)`. Re-run after *every* engine edit; it
caught nothing here only because the port was line-faithful — that is exactly
what the proof is for.

## The two live-e2e failures: both chain/transport contracts, not solver bugs

**1. `slider render timeout (15s)` — 6/6 CLI attempts, 0/3 manual spawns.**
Identical env (`JSDOM_PROXY`, site vars) spawned by hand solved every time;
the CLI ladder failed every time. The difference: the ladder ran a
`curl -x proxy` health check and spawned node immediately after — cheap
single-session proxies drop the connection churned too fast. Fixes:

- `time.sleep(4)` between health check and spawn (settle the session).
- Persist the engine's **full stdout** to `work/attempt_N/engine.log`
  (redacted) — without it the failure is one line and undiagnosable.
- Honest residual: post-fix the first attempt still failed once (proxy flake),
  then passed on the next run. Treat the settle as mitigation, keep the log.

**2. `page.evaluate: TypeError: Failed to fetch` — after T001.**
The verify URL was passed as an absolute `https://api.<host>/api/...` while
the auth session lives on `https://auth.<host>/sign-in/password`; a cross-origin
`fetch` from the page dies on CORS and looks like the captcha failed. The trial
contract was always **relative**: strip the origin, POST
`/api/experience/captcha/verify` against `page.url`'s origin, `credentials:
'include'`. `--verify-url` now accepts a path directly. Final e2e:
`T001 drag=150px → auth session → POST /api/experience/captcha/verify →
accepted`, certifyId `HrEC4ew208`, EXIT=0.

## Smaller bugs worth not re-deriving

| Symptom | Cause | Fix |
|---|---|---|
| `SyntaxError: unterminated f-string` on first run of a freshly written file | source generated inside `execute_code` with a non-raw `'''` body — `f"\n…"` became a real newline in the written file | write shipped source with `r'''…'''` (the JS file written that way was fine) or the dedicated write tool |
| `doctor` printed FAIL immediately after `doctor --fix` | provisioning ran *after* the checks | run `ensure_provisioned()` first, then check |
| `redact()` turned `http://user:pass@ip` into `***:***@ip` (scheme eaten) | the bare-proxy rule re-matched the already-redacted URL | exclude `/` from the password character class so `://…@` can't re-match |
| `NameError: urlsplit` at chain runtime despite `compileall` green | `compileall` checks syntax, not names | smoke-import every module after edits; run the chain path once |
| Zip shipped without `package.json` → `npm install` has nothing to install | `-x '*.json'` (intended for outcome artifacts) matched it too | exclude explicit artifact names (`solve_outcome.json`, `e2e_outcome.json`, `*.log`), never extension globs |

## Exit contract (observed live)

`0` solved (T001 **and** host `{"success":true}`) · `3` attempts exhausted
(ladder walked, none flagged — observed) · `4` risk hold (every candidate
`F015`) · `1` unhandled crash (observed once, before the CORS fix) ·
`doctor` `1` when NOT READY, `0` when READY. FAIL-closed per attempt: the
fresh `work/attempt_N/` dir is cleared before spawn and PASS is parsed from
*that* attempt's own stdout.

## Credential scan — 0 hits over 18 files in the shipped zip

- Scan the **extracted archive**, not the working tree: the tree contains
  `work/*/engine.log` with egress lines and outcome JSONs.
- Patterns: session e-mail, `user:pass@ip`, `sk-`/`ghp_`/`AKIA`/`xoxb-`,
  `Bearer <20+>`, key/password/secret assignments, PEM headers, env-token
  names (`ANTHROPIC_AUTH_TOKEN` — the AI-vision path this port deleted) —
  **plus the target's public site identifiers** (widget prefix, sceneId).
  Those two are not secrets, but zero hits proves no target is baked in and
  the tool is site-agnostic; they travel as runtime CLI/env values.
- One false positive was the redaction regex's own explanatory comment →
  reword the comment, never weaken the scan.
- Zip excludes: `node_modules/`, `sdk/` (re-fetched), `work/`,
  `__pycache__`/`*.pyc`, outcome artifacts.
