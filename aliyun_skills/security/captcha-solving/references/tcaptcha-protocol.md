# TCaptcha protocol notes

Verified against Tencent TCaptcha 2.0 on an OAuth password-creation step
(Jan 2026 session). Field names below are observed, not documented upstream.

## Getting the appid

Constructor hook, installed **before** the challenge is raised:

```js
window.__capturedAppid = null;
const Orig = window.TencentCaptcha;
window.TencentCaptcha = function (appid, cb, opts) {
  window.__capturedAppid = appid;
  return new Orig(appid, cb, opts);
};
window.TencentCaptcha.prototype = Orig.prototype;
```

Then complete the form step that triggers the challenge and read
`window.__capturedAppid`. Confirmed: works on first trigger, returns the bare
numeric appid string.

Why not grep the bundle: the captcha loader is lazy-loaded, so appids never
appear in the chunks fetched at page load. Two full-bundle greps returned
nothing before the hook approach worked in one shot.

## Script endpoints

- `https://turing.captcha.qcloud.com/TCaptcha.js` — loader
- `https://turing.captcha.gtimg.com/1/tcaptcha-frame.<hash>.js` — frame UI
- `https://turing.captcha.qcloud.com/tgJCap.<hash>.js` — referenced by
  prehandle as the `js` param. **Returns an empty body without the right
  Referer**; don't bother fetching it cold.
- `https://turing.captcha.gtimg.com/1/template/drag_ele.html` — the slider
  iframe document (cross-origin; parent page cannot read its DOM)

## prehandle response shape

JSONP: `_aq_<6 digits>({...})` — regex-strip the wrapper, then `json.loads`.

Observed top-level keys: `state`, `ticket`, `capclass`, `subcapclass`,
`sess`, `sid`, `data`, `uip`.

`data.comm_captcha_cfg`:
- `tdc_path` — relative URL of the behaviour collector, e.g.
  `/tdc.js?app_data=<sid>&t=<int>`
- `pow_cfg` — `{prefix, md5}`; solve `md5(prefix + str(nonce)) == md5_target`

`data.dyn_show_info`:
- `instruction` — e.g. `"拖动下方滑块完成拼图"` (slide the slider to complete
  the puzzle). Useful as a classifier signal.
- `bg_elem_cfg` — `{sprite_pos: [0,0], size_2d: [W,H], img_url, color_scheme}`.
  `img_url` carries `img_index=1`.
- `fg_elem_list` — element array, **order is not meaningful**
- `fg_binding_list` — present for slider type; master/slave pairs with
  `bind_type: DynBindType_DX_DX`. Presence of this key is the slider classifier.
- `sprite_url` — foreground sprite, `img_index=0`

### fg_elem_list — the field-shape traps

Three elements were observed. Types differ per element:

```json
{"id": 3, "sprite_pos": [0,422], "size_2d": [672,32], "init_pos": [0,422]}
{"id": 1, "sprite_pos": [140,490], "size_2d": [120,120], "init_pos": [50,103],
 "move_cfg": {"track_limit": "x>=50&&x<=552", "move_factor": [1,0],
              "data_type": ["DynAnswerType_POS"]}}
{"id": 2, "sprite_pos": [0,490], "size_2d": [130,70], "init_pos": [45,402],
 "move_cfg": {...}, "type": "slider"}
```

Two traps, both of which broke a real solver:

1. **Key name is `id`, not `elem_id`.** Solvers written against a different
   build expect `elem_id` → `KeyError: 'elem_id'`.
2. **Coordinates are arrays `[x, y]`, not `{"x":..,"y":..}` dicts.** Size too:
   `size_2d` is `[w, h]`, not `{width, height}`.

Make the parser accept both shapes (`elem.get("elem_id", elem.get("id"))`, and
a `_xy()` helper that handles dict or list). Do not assume the solver is
unusable because of this.

### Selecting the real piece

`fg_elem_list[0]` is element id=3 — a 672×32 caption bar with `init_pos`
outside the background bounds. Selecting it makes the matcher index an empty
patch → `IndexError: boolean index did not match ... size of axis is 0`.

Correct selector: the element whose raw `move_cfg.data_type` contains
`"DynAnswerType_POS"`. Fallback: an element whose `init_pos` + `size_2d` fits
inside the background and is smaller than the background in both dimensions.

## Image endpoints

`cap_union_new_getcapbysig?img_index=1&image=<hex>&sess=<sess>` → background
`cap_union_new_getcapbysig?img_index=0&image=<hex>&sess=<sess>` → sprite sheet

Both need `Referer: https://turing.captcha.gtimg.com/`.

Observed sizes: background PNG 672×390 (RGB), sprite 682×620 (RGBA). The
sprite holds the piece, the slider handle, and the caption bar.

### Coordinate-space mismatch

`bg_elem_cfg.size_2d` was `[672, 480]` while the downloaded background was
**672×390**. The extra height is the slider track: element id=2 sits at
`init_pos: [45, 402]`, below the image itself.

Consequence: piece coordinates live in image space (the piece's `init_pos`
always fit within 390), but the logical canvas is 480 tall. Derive which space
the answer needs from `init_pos` and `track_limit` (`x>=50&&x<=552` here —
552 = 672 − 120 piece width, confirming horizontal-only motion in image
pixels). Never assume.

## verify request

POST `application/x-www-form-urlencoded` to `cap_union_new_verify`, fields:
`ans`, `sess`, `pow_answer`, `pow_calc_time`, `collect`, `tlg`, `eks`.

- `Referer` = full entry URL, `Origin` = its origin. Cross-checked against the
  captcha registration; wrong values → `errorCode=12`.
- `ans` for slider: `[{"elem_id": 1, "type": "DynAnswerType_POS", "data": "x,y"}]`
  where `x,y` is the target top-left of the piece.
- `pow_calc_time`: report 300–500 ms. Sub-200 ms is flagged as non-browser.
- `tlg` = byte length of `collect`.

### Failure modes observed

| errorCode | When |
|---|---|
| `12` | **Treat as rate-limit / risk-control first**, not as a wrong answer — two independent write-ups plus repeated live batches where 3-of-3 detector agreement returned `12` and the *same* code path succeeded minutes later. Referer/Origin mismatch also yields it (and is worth ruling out once, cheaply), but a burst of `12` across unrelated challenges is the clock, not the payload. See `slider-captcha-solving/references/rate-limit-and-risk-control.md`. |
| `50` | Returned on a later attempt in the same session. Meaning not yet pinned down — candidate: risk/rate control after repeated attempts. |
| `0` | Success + `ticket` + `randstr` |
| **absent** — no verify request at all | prehandle issued `state:1, ticket:""`, `src_2=template/new_placeholder.html`. The widget never posts `verify`, so there is no code to read and no challenge to solve. Read `prehandle_resp` when a run reports `errorCode=None` with zero verify requests. |

`errMessage` came back empty in both failure cases, so the code alone carries
the signal.

**Open question — narrowed, not resolved:** gap detection was verified correct
by two independent detectors agreeing to within 1–3 px *and* by visual
inspection, yet verify still returned 12 then 50.

> **Update:** the `12` rows in that observation are now attributed to IP /
> volume rate-limiting (see the table above and the cross-reference), so they
> no longer count as evidence against detection *or* against the payload. What
> remains genuinely open is only the `50` case. Next step is unchanged — a
> field-by-field diff against a browser-solved request — but re-run it when
> `prehandle` is issuing real tickets, otherwise the diff captures a widget that
> never submits.

## Ground-truth capture recipe

```python
page.on("request",  lambda r: log_req(r)  if "verify" in r.url else None)
page.on("response", lambda s: log_resp(s) if "verify" in s.url else None)
```

Capture the real widget solving once, then diff your payload field-by-field:
headers (Referer/Origin), every POST field, and the response. This is the only
reliable way to settle `ans` coordinate space and the `collect`/`eks` contract.

Auth-page mechanics that cost time:
- Client-side-rendered pages need `wait_for_selector(sel, timeout=60000)`, not
  a fixed sleep — inputs are absent for several seconds.
- Custom ToS checkboxes expose **no** `input[type=checkbox]`. Click the
  agreement text node instead; without it the primary button is a no-op.

## DOM shim requirements for the collector

The behaviour collector is an obfuscated bytecode VM. Requirements to run it
outside a browser:
- `pretendToBeVisual: true`, `runScripts: "dangerously"`
- Patch `screen`, `innerWidth/innerHeight`, `devicePixelRatio`,
  `navigator.webdriver`
- Feed it `{elapsed_ms: "x,y"}` trajectory entries

If the collector fails, verify will reject regardless of how correct `ans` is.
