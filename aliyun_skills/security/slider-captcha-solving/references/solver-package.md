# Rebuilding a throwaway solver into a shippable tool — the `tcaptcha` package

Session detail behind `## Shipping the solver: rebuild clean, then prove
equivalence` in the SKILL. Shape: user handed over the trial-and-error knowledge
("lu kan dah tau gimana cara solvenya … lu bangun ulang tools source code") and
one hard constraint — **"pastikan tools tersebut bersih dari kredensial"**.

## Result

`/root/tcaptcha` — package `tcaptcha`, 13 modules, 1 571 lines + 138-line README,
built from the working throwaway script (`/root/jev_captcha.py`, 717 lines).

Run it with the interpreter that already carries the deps:

```bash
cd /root/tcaptcha
/root/crackTCaptcha/.venv/bin/python -m tcaptcha <subcommand>
```

| module | lines | responsibility |
|---|---:|---|
| `config.py` | 158 | env/CLI resolution, proxy parse, `redact()` |
| `browser.py` | 124 | `Session` ctx manager, asset prefetch + `route` |
| `network.py` | 97 | passive tap (prehandle/verify/images), URL fallback |
| `flows.py` | 101 | `@register("minimax-login")` adapter + `wait_for_widget` |
| `detect.py` | 274 | 5 detectors, `cluster()`, `candidates()` |
| `decision.py` | 155 | `build_request()` + `jev` / `heuristic` arbiters |
| `drag.py` | 75 | eased + jittered drag, logical→screen scale |
| `risk.py` | 102 | placeholder / `errorCode` 12 / 50 classification |
| `engine.py` | 236 | the attempt loop + retry policy |
| `cli.py` | 184 | `solve · doctor · detect · classify · env` |
| `log.py` | 39 | credential redaction on every line |

Exit-code contract: **`0` solved · `3` attempts exhausted · `4` risk hold**
(placeholder prehandle — nothing solvable, stop without burning attempts).
`doctor` returns 1 when NOT READY — read `$?` un-piped.

## Port-fidelity proof (the technique worth reusing)

Import the old module and the new package **in one process** and feed both the
same in-memory bytes; diff `json.dumps(x, sort_keys=True)`. Three passes:

1. **real image pair × two geometries** (stored `init_pos`, latest
   `init_pos`) → `detect()` output identical for all keys
   (`ncc/canny/contour/dark/hole/bg_size/init_pos/dark_peaks/hole_peaks/contour_votes`).
2. **forced consensus** — deep-copy the result and patch `ncc/canny/dark.x` to
   one x (plus `dark.corr`/`interior_edge`, which the original reads
   unconditionally and `KeyError`s without). This is the *only* way to
   exercise the non-empty drag-candidate path offline: the stored image pair
   legitimately yields "no cluster" → 0 legal candidates.
3. **decision state** — compare the same key set on both sides.

Two traps hit while writing that test:

- **Stored artifacts are not a valid oracle.** `bg.png`/`fg.png`/`detect.json`
  come from the attempt that last detected; `capture.json` is overwritten at the
  end of the run with the *final* prehandle (often a later refresh). Diffing
  new-code output against the stored file reported 5 bogus mismatches — every
  one of them a different `init_pos` (99 vs 159). Same inputs, or nothing.
- **The test can be the wrong one.** The first "state mismatch" was the test
  recomputing `pair_cluster_x` as `top["x"] if top else None` (the old rule)
  while the ported code had `if top and pair_ok`. Diff the *requests* the two
  `build_request()` implementations emit, never hand-derived fields.

Result line: `PORT RESULT: ALL PASS` across all three passes.

## Credential-clean verification (the user's constraint)

Nine patterns grepped over `tcaptcha/*.py`, all `0 hits`: e-mail domains,
known password literal, `user:pass@ip` proxy form, `password\s*=\s*["']…`,
literal `TCAPTCHA_*=` assignments. Only expected hits: the **legacy env
fallback** names (`JEV_EMAIL`/`JEV_PWD` read via `os.environ.get`), which are
names not values and belong there.

Conventions that make it hold:

- `tcaptcha env` prints an **empty** template (`TCAPTCHA_EMAIL=`, …).
- `log.redact()` rewrites `scheme://user:pass@host` → `***:***@host` inside any
  log message; outcomes carry `proxy: "direct"` or `host:port` only
  (`cfg.proxy_label`), never the URL.
- Proxy may arrive as `--proxy`, `--proxy-file` (random line), `TCAPTCHA_PROXY`
  or `TCAPTCHA_PROXY_FILE`.
- Credentials: `--email/--password` → `TCAPTCHA_EMAIL/TCAPTCHA_PASSWORD` →
  legacy `JEV_EMAIL/JEV_PWD`. **This host's env carries neither** (only
  `JEV_MODEL`, `ROUTER_API_KEY`, `TYPESAFE_API_KEY`), so `doctor` correctly
  reports missing credentials rather than guessing. For a local run, export the
  two vars by extracting them from an existing scratch script
  (`grep -oP '"input\[type=email\]","\K[^"]+' /tmp/tscan.py`) instead of
  re-typing them into a command.

## Bugs found and fixed during the build (all four were real)

| Symptom | Cause | Fix |
|---|---|---|
| every subcommand died before running | `flows.py` used `from ..config/log/network import …` inside a top-level package | `..` → `.` |
| `state identical: False` on an agreeing input | `pair_cluster_x` built from any top cluster, not just a 2+ pair | `if top and verdict["pair_ok"]` |
| `AttributeError: … 'PlaywrightContextManager' object has no attribute 'stop'` *after* a correct result | kept the CM instead of the object `.start()` returns | `self._pw = sync_playwright().start()` |
| `doctor` FAIL `playwright` despite install | `playwright.__version__` doesn't exist | `importlib.metadata.version()` |

## Verified live (2026-10-02)

- `doctor` → **READY** (python 3.12.3, playwright 1.63.0, opencv 5.0.0, numpy
  2.5.3, pillow 12.3.0, flow `minimax-login`, credentials set, arbiter
  `jev-decide on PATH`).
- e2e #1: `frame ok [initial] prehandle=1` → `[risk] prehandle kind=placeholder`
  → early stop; crashed only at teardown (the `.stop()` bug).
- e2e #2 after the fix: **exit 4**, no traceback, `outcome.json`
  `{"ok": false, "attempts": 1, "risk_hold": true}`, `history:
  [{"attempt": 1, "error": "placeholder prehandle"}]`.
- Risk window still open at 10:05 UTC (placeholder on direct); one-shot probe
  scheduled for a later window — schedule the probe, do not scan for a clean IP.

## Runtime provisioning: the gap the operator caught with one question

Handing over `tcaptcha_v1.0.0.zip` drew the reply *"itu source dah termasuk
minta browser apa kan?"* — and the honest answer was **no**. `Session.__enter__`
called `self._pw.chromium.launch(...)` straight onto whatever happened to be in
`~/.cache/ms-playwright/chromium-1243`; nothing in the source installed a
browser, and `doctor` did not check one. On a clean env the first `solve` dies
at launch. The tool shipped as "works on my box".

Fix, all of it landed the same session:

- `browser.py` gained `ensure_chromium(pw) -> str`:

  ```python
  exe = pw.chromium.executable_path
  if exe and os.path.exists(exe):
      return exe
  log("browser", "chromium missing -> running `python -m playwright install chromium`")
  res = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       capture_output=True, text=True, timeout=900)
  if not (exe and os.path.exists(exe)):
      raise RuntimeError(f"chromium still missing at {exe}: {tail.strip() or 'no output'}")
  ```

  Note `sys.executable -m playwright`, not a bare `playwright` on PATH — the
  tool runs inside a venv whose bin dir may not be the caller's `PATH`.
- Called from **two** sites: `Session.__enter__` (before `launch`) and a new
  `check("browser", browser_exe)` in `cmd_doctor`, so provisioning and
  verification are the same code. Doctor line: `OK browser:
  /root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`.
- `README.md` Install section rewritten: "Chromium is **provisioned on
  demand** … the browser download is the *only* thing this tool fetches on its
  own."

Three branches tested, all cost zero captcha hits:

| Branch | Input | Result |
|---|---|---|
| cold | `PLAYWRIGHT_BROWSERS_PATH=/tmp/pwtest` (empty dir) | `[browser] chromium installed` → 48 s, **658 MB**, exe returned ✅ |
| warm | real installed path | returns path immediately, no subprocess ✅ |
| fail | `executable_path=/tmp/definitely/not/here/chrome` | `RuntimeError: chromium still missing at …` ✅ |

(The warm branch alone would have passed even with a broken installer — the
cold branch is the only one that measures provisioning.) `compileall` and a
fresh `doctor` re-run after the change; credential re-scan still `0 hits`
(`__pycache__` excluded from the zip).

**The generalisation worth carrying:** the browser was only the most obvious of
the silent assumptions. The other hard dependencies in this tool are the
interpreter (`/root/crackTCaptcha/.venv/bin/python` — absolute path in the README
run line), the `jev-decide` CLI (labelled arbiter check), credentials (labelled
check) and `--proxy-file` (caller-supplied). Rule: **every dependency is either
provisioned on demand or is a labelled `doctor` check.** When a deliverable is
handed over, expect the next question to be exactly this shape — *"does it
include X?"* — and audit for the rest before it is asked.

## Line budget after provisioning

`browser.py` +3 imports/+30-line helper/+1 call, `cli.py` +11 (the `browser`
check), README +7. Zip rebuilt as `tcaptcha_v1.0.0.zip` (30 KB; 70 610 B across
16 files, `__pycache__` excluded) and re-attached.

Offline tests that cost **zero** captcha hits and should be re-run first
whenever the tool changes: `compileall`, `doctor`, `detect --bg --fg --piece`,
`classify --file`, the port-fidelity script.

## v1.1.0: patching new skill lessons into the shipped tool

Session 2 (`update patch tcaptcha juga dengan skill yang baru`): the skill had
grown three lessons after v1.0.0 shipped; the tool was patched to encode them,
version bumped to `1.1.0`, zip rebuilt as `tcaptcha_v1.1.0.zip` (33 445 B, 15
files). Pattern to reuse: **read the skill's own shipping/Nx sections against
the existing tree, turn each unencoded lesson into a patch, re-run the zero-hit
test suite, re-scan, re-zip.**

| Lesson (skill section) | Code change |
|---|---|
| proxy health-check + retry ladder (Nx §3–4) | `config.load_proxy_candidates()` (flag > env > shuffled file, all lines); `browser.probe_proxy()` = urllib `ipify` + `TCaptcha.js` through the proxy; `browser.select_healthy_proxy()` tries ≤4, `time.sleep(4)` settle, pins winner on `cfg`; called at top of `engine.solve()` — dead pool raises `ConfigError` → exit 2 before Playwright starts. `doctor` gained a `proxy health` check. |
| fail-closed freshness, baked into the tool (artifact pitfall) | `engine._scrub_stale()` unlinks `outcome.json/capture.json/verify_resp.json/decision.json/candidates.json` at every `solve()` start |
| full redacted transcript (exit-contract section) | `log.set_sink(out_dir/run.log)` — timestamped, appended, redacted; opened in `solve()` |
| redaction idempotency on both proxy forms (credential-free §2) | `config.BARE_CRED_RE = (?<![\w:/])([^\s:/@*]{1,64}):([^\s/@]{1,128})@` used by `redact()` **and** `log._scrub()` **and** `die()` |
| batch pacing | `inter_run_sleep` 20 → 60 s |

Redaction bugs found **by the unit test, not by review** (both survived the
original build's functional tests):

1. First `BARE_CRED_RE` allowed `*` in the user class → applying redaction to
   an already-redacted `http://***:***@host` re-matched from the second star:
   `http://****:***@1.2.3.4:3129`. Fix: exclude `*` (plus `/`, `:`) from the
   user class; the lookbehind alone is not enough.
2. `log._scrub`'s scheme loop appended `"***:***"` and then set `i = at + 1`,
   swallowing the `@` → `http://***:***1.2.3.4:3129`. Fix: append
   `"***:***@"`.

The test that catches both — assert **exact expected strings** for both forms,
`redact(redact(x)) == redact(x)`, `redact(redact(redact(x)))`, a two-form
message through `_scrub`, and `die()`'s stderr:

```python
t("scheme", redact("http://user:pass@1.2.3.4:3129"), "http://***:***@1.2.3.4:3129")
t("bare",   redact("user:pass@1.2.3.4:3129"), "***:***@1.2.3.4:3129")
t("idem-scheme", redact(redact("http://user:pass@1.2.3.4:3129")), "http://***:***@1.2.3.4:3129")
t("triple", redact(redact(redact("user:p@1.2.3.4:1"))), "***:***@1.2.3.4:1")
t("log-exact", _scrub("via http://user:pass@1.2.3.4:3129 and bare wu:p@5.6.7.8:8080 end"),
  "via http://***:***@1.2.3.4:3129 and bare ***:***@5.6.7.8:8080 end")
```

Verification that cost zero captcha hits:

- `compileall` **plus** `pkgutil.walk_packages` smoke-import of every module
  (`compileall` is green on a `NameError`).
- Live ladder: one real pool candidate → `probe_proxy` `ok=True (egress+target 200)`,
  `select_healthy_proxy` pins it.
- Dead ladder: two `127.0.0.1:1/2` candidates → `ConfigError` in 0.1 s;
  `solve` CLI → **exit 2**, stderr carries `no healthy proxy candidate (2 tried, ...)`,
  out dir contains **only `run.log`** (two FAIL lines) — no verdict artifact to
  misread.
- `doctor` → exit 1 (credentials genuinely absent from env — correct FAIL),
  `proxy health: 1/1 healthy`, `proxy assets: 4/4`.
- `classify` placeholder → exit 4; port-fidelity → `PORT RESULT: ALL PASS`.
- Credential scan (9 patterns): source tree **0 hits**, and again over the
  **unpacked zip** — scan the artifact you ship, not just the tree you edited.
- Fresh-unpack smoke test: unzip to a temp dir, `sys.path.insert(0, ".")`,
  smoke-import + `--version` + one offline subcommand. A zip that passes from
  the repo but not from a clean unpack is not shipped.
