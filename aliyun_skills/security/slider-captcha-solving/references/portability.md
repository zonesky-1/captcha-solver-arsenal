# Porting the solver to another TCaptcha host

Session context: operator sent the shipped zip back and asked *"ini sourcenya
buat web yang pake tcaptcha atau universal selama web itu pake tcaptcha"*.
Answer shape: **universal at the widget layer, one file per site.** This
reference holds the evidence and the adapter skeleton.

## Why the core is vendor-global, not site-specific

- `network.py` taps three Tencent endpoints by **path suffix**, not by host:
  `cap_union_prehandle` (JSONP → geometry + ticket), `getcapbysig?img_index=1`
  (background; other `img_index` → foreground), `cap_union_new_verify`
  (POST → `errorCode`). These are the same on every site that embeds the
  widget; `aid`, `domain`, `noheader` etc. ride along in the captured live
  URLs, so nothing is hardcoded per site.
- `wait_for_widget` resolves the frame by URL substring `captcha` or `gtimg` —
  covers `turing.captcha.qcloud.com` and `turing.captcha.gtimg.com` (both
  domains serve the same assets, which is why prefetch lists both).
- `detect` / `decision` / `drag` / `risk` operate on captured bytes and
  response codes only. They never see the host page.

## Flow adapter contract (the only per-site code)

```python
@register("situs-baru")
def situs_baru(page, cfg: Config, cap: dict, tag="initial"):
    """Walk the host page until it requests a captcha; return the frame."""
    cfg.require_credentials()
    NetworkTap.reset(cap)
    page.goto("https://...", wait_until="domcontentloaded", timeout=60000)
    # ...fill the trigger form / click the button that raises TCaptcha...
    return wait_for_widget(page, cap, cfg.frame_timeout, tag)
```

Rules the contract encodes (each one cost a debugging round — see SKILL.md):

1. `NetworkTap.reset` **before** navigation, or stale bytes from the previous
   challenge leak into the new one.
2. `wait_for_widget` returns only when the frame **and** `prehandle_resp` are
   both present; prehandle can trail the frame by a beat.
3. Retries call this function again — the widget's own refresh button does not
   produce a fresh challenge (it re-downloads the same session's images).
4. Credentials come from `Config`, never from the adapter.

Run: `python -m tcaptcha solve --flow situs-baru --max-attempts 2`.

## Hard limits when claiming universality

| Limit | Symptom | Remedy |
|---|---|---|
| Slider-type only | click/rotate TCaptcha variant: detector finds no gap, drag is meaningless | new detect + action path (out of current scope) |
| Prefetch hash-pinned to one widget version | `tcaptcha-frame.<hash>.js` cache-miss on a host running a different version → asset must be fetched live | degrade is pass-through fetch (OK unless proxy drops CONNECT — then auto-discover `<script src>` from the widget HTML and prefetch by pattern) |
| Not a TCaptcha at all | reCAPTCHA / hCaptcha / Turnstile | different tool class — external solver or `hunt-captcha-bypass` first |
| Host page flow differs | email/TOS/Continue sequence is MiniMax-specific | write the ~15-line adapter above |

## Answering "is it universal?" without overclaiming

Three buckets, in this order, each verified against the tree:

1. **Universal** — network/detect/decision/drag/risk/engine (paths above).
2. **Per-site** — `flows.py` registry; count how many adapters exist
   (`grep '@register' flows.py`) and name them.
3. **Not covered** — challenge types, widget-version drift, non-TCaptcha.

Then offer the concrete next step: a generic `--url` flow (URL + trigger
selector as CLI flags, no new code) plus script-`src` auto-discovery for
prefetch, which converts "universal in theory" into "universal in practice"
for any slider-TCaptcha host.

## Related skills inventory (grep of the skill tree, 2026-10-02)

Direct hits for `captcha|turnstile|geetest` in SKILL.md files:

- `security/slider-captcha-solving` (this skill) — empirical solver pitfalls.
- `security/captcha-solving` — vendor breadth + pure-HTTP protocol +
  `references/solver-repos.md` (three OSS repos field-tested).
- `hunt-captcha-bypass` — validation flaws: omit field, token replay,
  no binding, static value, audio, after-N-failures. Cheaper than solving;
  exhaust it first.

Incidental mentions only (not solving): `hunt-ato`, `hunt-brute-force`,
`hunt-auth-bypass`, `auth-flow-recon`, `hunt-forgot-password`.
