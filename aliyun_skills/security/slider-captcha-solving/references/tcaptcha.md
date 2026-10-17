# TCaptcha 2.0 — session notes (MiniMax account signup)

Live target exercised end-to-end: `account.minimax.io/unified-login` → password
step → TCaptcha appears. Full pipeline returned `errorCode "0"` + ticket and the
flow advanced to the next step. Numbers below are observed, not guessed.

## Getting the appid

The appid is not in the page HTML — it is passed to the widget constructor at
call time. Wrap it:

```js
window.__capturedAppid = null;
const Orig = window.TencentCaptcha;
window.TencentCaptcha = function (appid, cb, opts) {
  window.__capturedAppid = appid;
  return new Orig(appid, cb, opts);
};
window.TencentCaptcha.prototype = Orig.prototype;
```

Then trigger the flow and read `window.__capturedAppid`. Observed value:
`196931114`.

Widget resources load from `turing.captcha.qcloud.com/TCaptcha.js` and
`turing.captcha.gtimg.com/1/tcaptcha-frame.*.js`; the challenge iframe is
`https://turing.captcha.gtimg.com/1/template/drag_ele.html`.

## Signup flow gotchas (Playwright)

- The ToS control is **not** an `<input type=checkbox>`. It is a `<button>`
  sibling sitting *before* the `<p>` that holds the ToS text. Clicking the text
  does nothing and leaves `Continue` inert — a silent stall.

  ```js
  const p = [...document.querySelectorAll('p')]
    .find(e => (e.innerText || '').includes('I have read and agree'));
  const sib = p && p.parentElement.querySelector('button');
  if (sib) sib.click();
  ```

- The page is client-side rendered and slow. `wait_for_selector("input[type=email]", 60000)`
  is required; a fixed sleep yields an empty DOM.
- **Timing:** the challenge iframe appears ~6 s after the password `Continue`,
  `prehandle` fires around 3–4 s, and the background/foreground images download
  *after* `prehandle`. Poll for all three rather than sleeping once.

## Response shapes

`prehandle` returns JSONP — strip the callback (`_aq_NNNNN(...)`) before
`json.loads`. Relevant fields:

- `data.comm_captcha_cfg.pow_cfg` → `{prefix, md5}` (PoW; server checks elapsed
  time, a sub-200 ms `pow_calc_time` is flagged — report 300–500 ms)
- `data.comm_captcha_cfg.tdc_path` → `/tdc.js?...` (behaviour collector)
- `data.dyn_show_info.bg_elem_cfg.img_url` → `img_index=1` = background
- foreground sprite = same URL with `img_index=0`
- `data.dyn_show_info.fg_elem_list[]` → `{id, sprite_pos, size_2d, init_pos, move_cfg}`

**Select the piece by capability, not position:** the element whose
`move_cfg.data_type` contains `DynAnswerType_POS`. Observed layout on a live
challenge — `[3]` decorative bar at `init_y=422` (past the 390-px image height),
`[1]` the 120×120 piece, `[2]` the slider handle.

## Geometry

| Quantity | Observed |
|---|---|
| `bg_elem_cfg.size_2d` (logical) | `672 × 480` |
| Natural background image | `672 × 390` |
| Rendered `.tc-bg-placeholder` box | `340 × 242.84375` |
| Scale | `340 / 672 = 0.505952` |

`move_factor` is `[1, 0]` — the piece moves horizontally only, so the answer's
`y` should equal `init_pos.y`. `track_limit` is `x>=50 && x<=552`.

## Widget DOM (inside the cross-origin iframe)

| Purpose | Selector | Observed box |
|---|---|---|
| Background | `.tc-bg-placeholder` (alt `.tc-bg-img`) | `{x:10, y:69.78, w:340, h:242.84}` |
| Slider handle | `.tc-fg-item` (alt `.tc-slider-normal`) | `{x:32.77, y:273.17, w:65.77, h:35.41}` |
| Loading overlay | `.tc-cover.tc-loading` | gate on `is_visible()` before dragging |
| Failure / success covers | `.tc-cover.tc-fail`, `.tc-cover.tc-success` | |

Pitfall: the handle sits **inside** the image area (y 273 < image bottom 312),
so a "find the element below the background" search never finds it. Query the
class directly.

### `.tc-action--refresh` does not issue a new challenge

Clicking it (element click or raw coordinate click) keeps the `prehandle` count
fixed at 1 while `getcapbysig` responses double — it re-fetches the *same*
session's images. There is no widget-level way to move to a fresh image from
inside the widget; re-run the entry flow instead. Full transcript in
[`consistency-and-retry.md`](consistency-and-retry.md).

## `verify`

The widget POSTs `verify` **itself** after a real drag — the automation does not
build the payload. Body fields: `ans`, `collect`, `tlg`, `eks`, `sess`,
`pow_answer`, `pow_calc_time`, `tkid`.

- `ans` format: `[{"elem_id":1,"type":"DynAnswerType_POS","data":"506,122"}]`
- success: `{"errorCode":"0","randstr":"@XDF","ticket":"<144 chars>",...}`
- `errorCode: 12` → **IP / fingerprint rate-limit (risk control)**, *not* a wrong
  answer and not a Referer-Origin mismatch. An earlier revision of this note
  claimed both; that attribution is withdrawn — several unrelated challenges in
  a burst returned `12` with 3-of-3 detector agreement, then the same code path
  succeeded once the window passed. See
  [`rate-limit-and-risk-control.md`](rate-limit-and-risk-control.md).
- `errorCode: 50` → observed on repeated failures (risk/rate path)

**Capture by URL path, not substring.** A `cap_monitor` request carries
`...cap_union_new_verify` inside its `data=` query. A naive
`"cap_union_new_verify" in url` filter matches it and its empty body overwrites
the real response:

```python
path = urllib.parse.urlparse(resp.url).path
if path.endswith("cap_union_new_verify"): ...
```

## Detection results observed

| Run | NCC | Canny | Contour | Outcome |
|---|---|---|---|---|
| 1 | 213 | 525 | — | 312 px disagreement; edge hit the decoy → reject |
| 2 | 387 | 388 | rejected (spread 167) | consensus 2/3 → drag |
| 3 | 504 | 505 | none | consensus → drag → **`errorCode 0`** |

On the successful run the widget itself computed `506,122` against a detected
target of `504` — **2 px**, i.e. detection was comfortably inside tolerance.

## Pure-HTTP solver status

`hailan09/crackTCaptcha` (prehandle → getcapbysig → verify, TDC.js via jsdom)
was made to *run* but never to pass: two local patches were needed —
`fg_elem_list` parsing (`elem_id` vs `id`, `{x,y}` dict vs `[x,y]` array) and
piece selection — after which it returned `errorCode 12` then `50`. Cause was
**not** gap detection (verified visually correct). Remaining suspects are the
trajectory/`collect`/`eks` behaviour blob or TLS/fingerprint risk. In-browser
drag succeeded on the first attempt, so prefer the browser route unless pure
HTTP is a hard requirement.

**Re-read that `12`:** with the rate-limit correction above, the pure-HTTP run's
first failure is now most likely the IP/fingerprint window rather than the
payload. Do not use a `12`-ending run as evidence that the behavioural blob is
wrong — re-test the pure-HTTP path in isolation, on a cooled-down IP and with
pacing, before blaming `collect`/`eks`.
