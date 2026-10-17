# Open-source slider solver repos

Comparison from actually cloning and running three repos against a live TCaptcha
deploy. Pick by how much patching you are prepared to do.

## 1. `hailan09/crackTCaptcha` — closest to working, needs 2 patches

Python 3.10+ / Node 18+, GPL-3.0. Pure HTTP (no Selenium/Playwright):
`prehandle → getcapbysig → verify`, with the vendor's own behaviour collector
run inside Node + jsdom. TLS impersonation via `wreq` (Chrome emulation) — plain
`httpx`/`requests` gets 403, which is the whole reason this dependency exists.

Covers four challenge types: slider, icon_click, word_click, image_select.
Slider uses alpha-masked RGB/NCC template matching. word_click ships bundled
ONNX models (YOLO + Siamese) and is the author's best-tested path; slider is
explicitly marked "insufficiently tested" in its own README.

```bash
git clone https://github.com/hailan09/crackTCaptcha
cd crackTCaptcha && python3 -m venv .venv && . .venv/bin/activate
pip install -e .                              # slider only; add [word-click] etc.
cd src/crack_tcaptcha/tdc/js && npm install   # behaviour-collector bridge
```

CLI:
```bash
crack-tcaptcha solve --appid <APPID> --entry-url <PAGE_URL> --retries 3 --json
```

**Patches required against current TCaptcha 2.0** (both verified):

1. `src/crack_tcaptcha/client.py` — parser assumes `elem["elem_id"]` and
   `{"x","y"}` dicts. Current responses use `id` and `[x, y]` arrays. Replace
   the `fg_list` construction with a shape-tolerant helper:
   ```python
   def _xy(v):
       if isinstance(v, dict): return int(v["x"]), int(v["y"])
       return int(v[0]), int(v[1])
   # elem_id: elem.get("elem_id", elem.get("id"))
   ```
2. `src/crack_tcaptcha/pipelines/slide.py` — piece selection uses
   `pre.fg_elem_list[0]`, which is a caption bar, not the puzzle piece. Add a
   `_select_piece()` that picks the element whose raw
   `move_cfg.data_type` contains `DynAnswerType_POS`.

After both patches the full chain runs: prehandle → image download → NCC →
TDC collect → verify POST. Verify returned `errorCode=12` then `50`; gap
detection itself was independently confirmed correct. So: **protocol wiring and
detection are solved; the behavioural payload remains the open item.**

Architectural note: `AGENTS.md` in that repo documents the load-bearing
constraints (TLS fingerprint mandatory, classifier rule order, PoW timing
shaping, JSONP unwrap). Read it before changing anything.

## 2. `lcyyzy/CaptchaCracker` — best detection algorithm, dead driver

Python 3.6-era, 2019, no packaging, mixes Python 2 syntax. The driver
(`cracker.py`) is a Selenium script hard-wired to a specific registration URL
with embedded SMS-pool credentials — useless as-is, do not run it.

Value is entirely in `util.py`:
- `cv2.GaussianBlur → cv2.Canny(gray, 250, 250)` on the background
- piece contour loaded as a binary operator matrix
- cross-correlation: slide the operator over the Canny image, argmax = gap

```python
img_canny = cv2.Canny(cv2.cvtColor(cv2.GaussianBlur(img, (3,3), 0),
                                   cv2.COLOR_BGR2GRAY), 250, 250)
(x, y), _ = best_match(img_canny, operator)
```

Also handles the cut-and-reshuffled background case (background delivered as
slices, reassembled client-side) by inverse-transforming the per-tile offsets
from the DOM — worth stealing if a target does that.

Its `best_match` is a pure-Python double loop over the whole image. Reimplement
with `cv2.matchTemplate(edges, contour_img, cv2.TM_CCORR)` — ~0.01 s vs minutes.

## 3. `duckzhao/tencent_crack_captcha` — cleanest reference for the 3 methods

Small, focused, Python 3 + OpenCV 4 + numpy. Only does gap-coordinate
recognition; no protocol, no payload, no verification. Three independent
implementations of the same job:

- `findContours.py` — contour detection
- `threshold.py` — thresholding
- `matchTemplate.py` — template matching

Also ships `wangyi/yd_hk.py` for NetEase Yidun. Useful as a second opinion when
the other two repos disagree, and the sample images (`1.jpg` … `10.jpg` with
labelled `*_1.png` outputs) are a ready-made regression set for validating a
detector offline before touching a live target.

## Choosing

| Situation | Reach for |
|---|---|
| Need an end-to-end solve (protocol + payload) | crackTCaptcha, after the 2 patches |
| Need the best gap detector / want to validate offline | duckzhao's 3 methods + its sample images |
| Background is cut into reshuffled slices | lcyyzy's inverse-transform approach |
| Word/icon/image-select challenge | crackTCaptcha `[word-click]` (bundled ONNX) |
| reCAPTCHA / hCaptcha / Turnstile | none of these — external service, budget-gated |

## 4. Aliyun-side repos (added after a live Aliyun INPAINTING engagement)

| Repo | What it is | Verdict from live use |
|---|---|---|
| `xaoLiu1222/aliyun-captcha-v3-inpainting-solver` | jsdom protocol solver (no browser): env patches, `isTrusted` re-patched *after* SDK load, quadratic drag inverse, human drag + ±15 search | Produced 3/3 T001 against a live Logto-hosted scene once patched: `detectGap` → local capsolver detector, `hpagent` on the http/https globalAgent for proxy egress |
| `Sansa-Organisation/capsolver` | Self-hosted FastAPI for all Aliyun V3 types (`INPAINTING/SLIDER/ICON/NOCAPTCHA/SMART`); `solver.py::detect_gap` (mvar/depth/boundary-ratio/gabor + scene classification) | Best INPAINTING gap detector tested (conf 0.89–0.99 live); importable as a plain library via `solve_gap_from_files`. Its README is also the cleanest statement of the F014/F015 split |
| `B00H0O/Aliyun-Solver` | `solver.js` + bundled `AliyunCaptcha.js` | Thin; no protocol layer worth lifting |
| `chiaweilee/vue-nocaptcha` | Vue 2 wrapper around the 2018 `nc.js` NoCaptcha SDK | Integration reference only |

Aliyun specifics none of the TCaptcha repos cover: quadratic drag→position
mapping, the F014/F015 code split, and the challenge-lifetime/staleness trap —
see `slider-captcha-solving/references/aliyun-inpainting.md`.

## Cross-repo validation habit

Run two detectors against the same downloaded background/sprite pair before
believing either. NCC and Canny×contour agreed within 1–3 px on every trial
tested — and both correctly ignored a decoy silhouette drawn at the piece's
start position. A single detector matching "confidently" is exactly how you
submit a wrong answer.
