# Consistency runs, refresh probe, detector measurements

Session-specific evidence behind the `## Retry by re-entering the flow` and
`## Detect with a consensus` sections of the SKILL. Numbers are as recorded.

## The refresh probe

`refresh_probe.py`: drove the MiniMax signup flow to the TCaptcha iframe, counted
`cap_union_prehandle` requests and `getcapbysig` responses, then tried the widget's
own refresh control both ways.

```
after load: prehandles = 1, images = 4
--- attempt 1 sel=[class*=tc-action--refresh] found=True
  clicked
  prehandles before=1 after=1 images=4
  after raw click: prehandles 1 -> 1
--- attempt 2 sel=.tc-action--refresh found=True
  clicked
  prehandles before=1 after=1 images=8
  after raw click: prehandles 1 -> 1
```

Refresh-ish elements present in the iframe: `.tc-action--refresh`,
`.tc-action--close`, `.tc-action-icon`, `.tc-action.verify-btn` (hidden).

Conclusion: click succeeds, no new session is minted, the same images are
fetched a second time. Retry must re-enter the entry flow.

## Detector measurements on a confirmed-bad challenge

Challenge where all three detectors said `x≈285` and the submit came back
`errorCode 12`; visual inspection put the real hole at `x=440–540`.

| x | outline-edge mean | bright-rim mean | contrast (ring−inside) | interior | ring | identity |
|---|---|---|---|---|---|---|
| 285 | 103.8 | 53.2 | 34.3 | 39.6 | 73.9 | decoy |
| 286 | 118.6 | 57.1 | 30.9 | 41.2 | 72.1 | decoy |
| 440 | 78.2 | 31.4 | 22.7 | 71.5 | 94.3 | hole edge |
| 470 | 80.6 | 29.2 | −6.7 | 101.0 | 94.3 | hole |
| 500 | 61.9 | 15.3 | −6.9 | 101.9 | 95.0 | hole |
| 530 | 62.6 | 17.1 | 20.7 | 78.9 | 99.6 | hole edge |

Outline-match band (`TM_CCORR` of Canny against the piece-outline template), same
units for every candidate:

```
x= 286 band=45257396   <- decoy, STRONGEST
x= 285 band=39600224   <- decoy
x= 482 band=31537122   <- hole
x= 470 band=30756822   <- hole
x= 440 band=29846474   <- hole edge
x= 530 band=24254324
x= 500 band=23604076
```

Both the decoy and the hole carry a bright rim, so the outline ranks the decoy
first. The interior/ring ratio separates them cleanly in *this* image
(0.54 vs 1.07–1.14) but did not survive live trials — see below.

## The interior-ratio override: 0/5

Detector "hole": Canny-edges × piece-outline template → local maxima on the
piece row → rank survivors by interior/ring ratio. Used as an *override* when it
disagreed with the shape consensus.

| Run | Round | ncc | canny | dark | hole | Target used | Result |
|---|---|---|---|---|---|---|---|
| T1 | a1 | 291 | 292 | 501 | 74 (r 1.443) | hole | `50` |
| T1 | a2 | 282 | 283 | 282 | 505 (r 1.166) | hole | `50` |
| T1 | a3 | 330 | 330 | 490 | 409 (r 1.219) | hole | `50` |
| T4 | a1 | 336 | 418 | 415 | 446 (r 0.929) | hole | `50` |
| T4 | a2 | 270 | 269 | 267 | 502 (r 1.048) | hole | `50` |
| T2 | a1 | 300 | 301 | 299 | none | shape consensus | **`0`** |
| T3 | a1 | 438 | 439 | 441 | none | shape consensus | **`0`** |
| T4 | a3 | 213 | 305 | 305 | none | shape cluster (canny+dark) | **`0`** |

`x=74` is the false positive that motivated "filter on ratio, then rank by
outline": ratio ≈ 1 also holds for any texture-free patch, and 74 sits next to the
piece start (`init_x=50`), so the drag was only 24 px.

First fix applied (`good.sort(key=-band)` before picking) was kept in the code
but the override itself was disabled — the record above is what decided it.

## Consensus failures without the override

> **Retracted reading.** These rows were titled "consensus failures" on the
> assumption that `errorCode 12` meant "your coordinate was wrong". `12` is a
> rate-limit / risk-control code, so this table no longer supports that
> conclusion — see [`rate-limit-and-risk-control.md`](rate-limit-and-risk-control.md).
> The rows are kept as a record of what the detectors returned, not as evidence
> that the detectors were wrong.

| Run | Round | ncc | canny | dark | Agreement | Code |
|---|---|---|---|---|---|---|
| R3 | a1 | 339 | 339 | 534 | shape 2-of-2 | `12` |
| R3 | a3 | 459 | 460 | 315 | shape 2-of-2 | `12` |
| S4 | a1 | 321 | 320 | 322 | 3-of-3 | `12` |
| S4 | a3 | 285 | 286 | 285 | 3-of-3 | `12` |
| F1 | a2 | 411 | 411 | 413 | 3-of-3 | `12` |
| F1 | a3 | 234 | 234 | 232 | 3-of-3 | `12` |

Note that R3/S4/F1 are three separate runs each landing on `12` repeatedly —
that clustering across runs is the rate-limit signature, not a detection-quality
signal. The structural-independence warning in the SKILL still holds, but it now
rests on the decoy measurements above rather than on these rows.

## Follow-up batch: 7/10 under identical code

Runs I1–I10, one process launch each, same build as the G/H tally:

| Run | I1 | I2 | I3 | I4 | I5 | I6 | I7 | I8 | I9 | I10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Result | ok | ok | ok | ok | crash | `12`×3 | `12`×3 | ok | ok | ok |

I5 was a corrupt image byte stream (`PIL.UnidentifiedImageError`), unrelated to
the captcha. I6/I7 are contiguous `12` bursts with tight consensus inside each;
I8–I10 succeeded immediately afterwards with the same detector stack. A follow-up
batch run right after those bursts produced `12` on **all four** attempts of one
run and then two runs with **no captcha iframe at all** — the placeholder state.

Read: 100% on the G/H batch was real but did not mean "immune to rate limits".
Pace the batches.

## Reject-then-re-enter rounds that paid off

Scattered votes rejected cleanly, the flow was re-entered, and the next image
resolved:

| Run | a1 | a2 | a3 | Result |
|---|---|---|---|---|
| R1 | 243/354/518 → refresh | 489/489/489 → drag | — | **`0`** |
| H2 | 429/319/518 → refresh | 210/288/289 → drag | — | **`0`** |
| H5 | images missing | 345/345/483 → refresh | 405/404/402 → drag | **`0`** |
| G1 | 360/359/544 → refresh | 498/197/498 → drag | — | **`0`** |

## Final tally on the stable build

Runs G1–G4 and H1–H6, one process launch each, success defined as `errorCode 0`
parsed from that run's own log:

```
runs            : 10
solved (ec=0)   : 10
solve rate      : 100%
total drag exec : 10
drag accuracy   : 100%
```

The build under test had: per-challenge capture reset gated on a new `prehandle`,
re-entrant entry function used for every retry, image-URL re-fetch fallback,
frame retained while waiting for a late `prehandle` response, and the interior-ratio
override disabled.

## Reproduction

```
cd /root && /root/crackTCaptcha/.venv/bin/python jev_captcha.py
# artifacts per run: /tmp/cap/{bg,fg,annotated}.png, detect.json,
#                    decision.json, verify_resp.json, verify_post.txt
```
