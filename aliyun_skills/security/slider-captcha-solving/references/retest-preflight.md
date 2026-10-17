# Scheduled retest preflight — replicate a broken `doctor`, account the hit budget

Session detail behind the `## Bounded retest runs` section of the SKILL.
Shape: a cron-delivered retest of a TCaptcha risk window, hard budget of
**max 2 captcha hits**, conditional STOPs (credentials absent, `risk_hold`
placeholder, `errorCode 12` → report only), no source edits allowed outside
logs in the artifact dir.

## What the mandated commands returned

```bash
cd /root/tcaptcha && /root/crackTCaptcha/.venv/bin/python -m tcaptcha doctor
# Traceback ... cli.py line 16: from . import __version__, flows
#   flows.py line 16: from ..config import Config
# ImportError: attempted relative import beyond top-level package   (exit 1)
```

Because `cli.py` imports `flows` at module scope, **every** subcommand
(`doctor`, `solve`, `env`, `classify`) dies before doing anything — so the
`solve` step could not have run either, independent of the credential STOP.

Diagnosis pattern (generic):

```bash
grep -rn 'from \.\|import \.' <pkgdir>/*.py     # list every relative import
```

13 of 16 imports in the package used single-dot `from .x import …`; three
lines in one file (`flows.py:16-18`) used `from ..config/log/network import …`.
The outlier is the bug. Fix is `..` → `.` (one character ×3) — **report it,
do not apply it during a retest window**: the mid-retest patch changes the
artifact under measurement and the next scheduled run must show the tool as
delivered.

## Replicating the doctor offline (zero network, zero hits)

Read `cmd_doctor` in the tool's `cli.py` to enumerate its checks, then
reproduce them with one `python -c` block, printing `OK/FAIL label: detail`
per item. The enumeration for this tool was:

| Check (as coded) | Result | Reading |
|---|---|---|
| `sys.version.split()[0]` | OK 3.12.3 | — |
| `__import__("playwright").__version__` | **FAIL** `AttributeError` | **stale check**: `importlib.metadata.version("playwright")` = 1.63.0 and `~/.cache/ms-playwright/chromium-1243` + `chromium_headless_shell-1243` present. Dependency is installed; the *check expression* is wrong for this version. |
| `cv2.__version__` / `numpy` / `PIL` | OK 5.0.0 / 2.5.3 / 12.3.0 | — |
| `", ".join(flows.available())` | **FAIL** ImportError | the real blocker (above) |
| credentials env vars | **FAIL** `TCAPTCHA_EMAIL`/`TCAPTCHA_PASSWORD` missing; legacy `JEV_EMAIL`/`JEV_PWD` fallback also missing; tool has no dotenv loader; presence-only grep of `.bashrc`/`config.yaml`/`.env` found nothing | **STOP condition → report, do not guess** |
| proxy env | INFO (none set) | — |
| arbiter (`heuristic`) | OK, no external dependency | — |

Rule distilled: **a FAIL from the replicated check table must be classified
before it reaches the report** — *stale check expression* vs *missing
dependency* vs *genuine blocker*. Only the third stops the run; `importlib.metadata`
+ an install-path glob settles the second in one command.

## Mechanics that silently corrupt the report

- `cmd 2>&1 | tail -15; echo EXIT=$?` reports **tail's** exit code, not the
  tool's. Redirect to files under the artifact dir, then `EC=$?` immediately.
- The artifact dir (`/tmp/tcaptcha`) **did not exist** — the redirect itself
  failed (`No such file or directory`) and the first "doctor run" logged
  nothing. `mkdir -p` it before the first redirect.
- `outcome.json` absent after a STOP is *expected* (never written), not
  evidence of a prior run — say so explicitly rather than leaving it implied.
- Env-var probes: `env | grep -i tcaptcha` also matches `PWD=/root/tcaptcha`
  via the *value*; match on the name (`grep -E '^(TCAPTCHA|JEV)_'`) and print
  presence only, never values.

## Report checklist for a scheduled retest (deliverable shape)

Language/format follows whatever the job prompt specifies (this run: Bahasa
Indonesia, ringkas, bullet).

- **Hasil doctor:** OK/FAIL *per item*, with the stale-vs-missing
  classification inline.
- **Hasil e2e:** sukses/gagal, errorCode, jumlah attempt, jenis prehandle
  (challenge vs placeholder), dan **satu baris** kesimpulan: apakah risk
  window sudah reda atau belum (or "belum bisa dinilai" if STOP fired).
- **Exit code tool** — the *tool's*, captured without a pipe.
- **Hit accounting:** "N dari M pukulan terpakai".
- Compliance line: source tidak diubah; hanya log di artifact dir yang
  ditulis; tidak ada kredensial/proxy dalam laporan.
- Blockers for the next window, as actionable fixes (import line to correct,
  env vars to export) — without applying them yourself.

## Status after the window (fixed 2026-10-02, between windows — never mid-retest)

Everything above was reported, not patched, and then corrected in a normal work
turn. A later reader should not treat these as still-open:

| Finding recorded here | What happened next | Verified by |
|---|---|---|
| `flows.py` used `from ..config/log/network import …` → every subcommand died | `..` → `.` (3 lines) | `compileall` + all five subcommands run |
| `playwright.__version__` stale check | `importlib.metadata.version("playwright")` → 1.63.0 | `doctor` prints `OK playwright` |
| credentials absent | **still absent, by design** — host env carries neither `TCAPTCHA_*` nor `JEV_EMAIL`/`JEV_PWD` (only `JEV_MODEL`, `ROUTER_API_KEY`, `TYPESAFE_API_KEY`) | `doctor` = `FAIL credentials` until exported |
| (not seen here) teardown crash | `sync_playwright()` was stored as a context manager; `.stop()` raised `AttributeError` *after* a correct result | `SESSION EXIT CLEAN` |

Post-fix state: `doctor` → **READY**; live run → `frame ok [initial]`, risk gate
reads the placeholder and exits **4** with `outcome.json` (`risk_hold: true`)
rather than crashing or burning attempts.

Supply the credentials for a *local* run by extracting them from an existing
scratch script instead of retyping them into a command:

```bash
export TCAPTCHA_EMAIL=$(grep -oP '"input\[type=email\]","\K[^"]+' /tmp/tscan.py | head -1)
export TCAPTCHA_PASSWORD=$(grep -oP '"input\[type=password\]","\K[^"]+' /tmp/tscan.py | head -1)
```

Never place those values in a report, a log line or a commit. Module map, exit
codes and the fidelity proof for the rebuilt tool live in
[`solver-package.md`](solver-package.md).
