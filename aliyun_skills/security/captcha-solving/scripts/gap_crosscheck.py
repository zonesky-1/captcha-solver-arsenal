#!/usr/bin/env python3
"""Cross-validate two independent slider-CAPTCHA gap detectors.

Run this on a downloaded background/sprite pair BEFORE trusting any answer.
Two detectors must agree within a few pixels; agreement is the confidence
signal, and it is what catches a decoy silhouette drawn at the piece's start
position.

Usage:
    python3 gap_crosscheck.py --bg bg.png --fg sprite.png \
        --sprite-pos 140,490 --piece-size 120,120 \
        [--init-pos 50,103] [--annotate out.png]

Exits 0 when the detectors agree, 1 when they disagree (do NOT submit) or on
bad input. argparse also exits 2 on malformed flags.

Requires: numpy, Pillow, opencv-python-headless.
"""
from __future__ import annotations

import argparse
import io
import sys

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    sys.stderr.write("missing dependency: pip install opencv-python-headless\n")
    sys.exit(2)
try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.stderr.write("missing dependency: pip install Pillow\n")
    sys.exit(2)

TOLERANCE_PX = 5


def _pair(text: str, name: str) -> tuple[int, int]:
    try:
        x, y = (int(v) for v in text.split(","))
    except ValueError:
        raise SystemExit(f"{name} must look like 'x,y' (got {text!r})") from None
    return x, y


def load_rgb(path: str) -> np.ndarray:
    with open(path, "rb") as handle:
        return np.array(Image.open(io.BytesIO(handle.read())).convert("RGB"))


def load_rgba(path: str) -> np.ndarray:
    with open(path, "rb") as handle:
        return np.array(Image.open(io.BytesIO(handle.read())).convert("RGBA"))


def crop_piece(fg: np.ndarray, sprite_pos: tuple[int, int], size: tuple[int, int]) -> np.ndarray:
    px, py = sprite_pos
    w, h = size
    if py + h > fg.shape[0] or px + w > fg.shape[1]:
        raise SystemExit(f"piece box {sprite_pos}+{size} falls outside sprite {fg.shape[1]}x{fg.shape[0]}")
    return fg[py : py + h, px : px + w]


def detector_ncc(bg: np.ndarray, piece: np.ndarray, init_pos: tuple[int, int] | None = None) -> tuple[int, int, float]:
    """Alpha-masked RGB normalised cross-correlation (independent method A).

    The coarse pass sweeps a single row; pass ``init_pos`` so it sweeps the
    piece's start row instead of y=0. Without it the coarse x can lock onto a
    feature that only exists at the top of the image, and the fine pass (whose
    x-range is derived from coarse) then never reaches the real gap.
    """
    rgb = piece[:, :, :3].astype(np.float32)
    mask = piece[:, :, 3] > 128
    if mask.sum() < 100:
        raise SystemExit("piece has almost no opaque pixels — check sprite_pos/size")

    flat = rgb[mask]
    centred = flat - flat.mean()
    norm = float(np.sqrt((centred**2).sum())) + 1e-8
    bg_f = bg[:, :, :3].astype(np.float32)
    ph, pw = piece.shape[:2]
    bh, bw = bg_f.shape[:2]
    coarse_y = 0 if init_pos is None else max(0, min(bh - ph, init_pos[1]))

    def score_at(x: int, y: int) -> float:
        patch = bg_f[y : y + ph, x : x + pw]
        if patch.shape[:2] != (ph, pw):
            return -2.0
        pv = patch[mask]
        pc = pv - pv.mean()
        return float((pc * centred).sum() / ((np.sqrt((pc**2).sum()) + 1e-8) * norm))

    best = (-2.0, 0, coarse_y)
    for x in range(0, bw - pw + 1, 4):          # coarse: stride 4 on one row
        s = score_at(x, coarse_y)
        if s > best[0]:
            best = (s, x, coarse_y)

    cx = best[1]
    best = (best[0], cx, coarse_y)
    for y in range(0, bh - ph + 1):             # fine: ±6 x, all y
        for x in range(max(0, cx - 6), min(bw - pw - 1, cx + 7) + 1):
            s = score_at(x, y)
            if s > best[0]:
                best = (s, x, y)
    return best[1], best[2], best[0]


def detector_canny(bg: np.ndarray, piece: np.ndarray) -> tuple[int, int, float]:
    """Canny edge x piece contour cross-correlation (independent method B)."""
    _, alpha = cv2.threshold(piece[:, :, 3], 128, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(alpha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise SystemExit("no contour found in piece alpha channel")
    template = np.zeros_like(alpha)
    cv2.drawContours(template, contours, -1, 255, 2)

    gray = cv2.cvtColor(cv2.GaussianBlur(bg, (3, 3), 0), cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 150, 250)
    if template.shape[0] > edges.shape[0] or template.shape[1] > edges.shape[1]:
        raise SystemExit("piece larger than background — wrong inputs")
    result = cv2.matchTemplate(edges, template, cv2.TM_CCORR)
    _, _, _, loc = cv2.minMaxLoc(result)
    return loc[0], loc[1], 0.0


def annotate(bg: np.ndarray, piece: np.ndarray, boxes: dict[str, tuple[int, int]], out: str) -> None:
    canvas = Image.fromarray(bg)
    draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(canvas)
    w, h = piece.shape[1], piece.shape[0]
    colors = {"NCC": (0, 255, 0), "Canny": (0, 160, 255)}
    for name, (x, y) in boxes.items():
        draw.rectangle([x, y, x + w, y + h], outline=colors.get(name, (255, 0, 0)), width=3)
    canvas.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bg", required=True, help="background image (img_index=1)")
    ap.add_argument("--fg", required=True, help="sprite sheet image (img_index=0)")
    ap.add_argument("--sprite-pos", required=True, help="piece origin in the sprite, e.g. 140,490")
    ap.add_argument("--piece-size", required=True, help="piece size, e.g. 120,120")
    ap.add_argument("--init-pos", help="piece start position, for reference only")
    ap.add_argument("--annotate", help="write an annotated PNG here")
    ap.add_argument("--tolerance", type=int, default=TOLERANCE_PX)
    args = ap.parse_args()

    bg = load_rgb(args.bg)
    fg = load_rgba(args.fg)
    piece = crop_piece(fg, _pair(args.sprite_pos, "--sprite-pos"), _pair(args.piece_size, "--piece-size"))

    init = _pair(args.init_pos, "--init-pos") if args.init_pos else None
    nx, ny, ncc = detector_ncc(bg, piece, init)
    cx, cy, _ = detector_canny(bg, piece)
    delta = max(abs(nx - cx), abs(ny - cy))

    print(f"background      {bg.shape[1]}x{bg.shape[0]}")
    print(f"piece           {piece.shape[1]}x{piece.shape[0]}")
    if args.init_pos:
        print(f"init_pos        {args.init_pos}")
    print(f"detector A NCC  ({nx},{ny})  score={ncc:.3f}")
    print(f"detector B Canny({cx},{cy})")
    print(f"delta           {delta}px  (tolerance {args.tolerance}px)")

    if args.annotate:
        annotate(bg, piece, {"NCC": (nx, ny), "Canny": (cx, cy)}, args.annotate)
        print(f"annotated       {args.annotate}")

    if delta > args.tolerance:
        print("DISAGREE — do not submit; inspect for a decoy silhouette or a")
        print("wrong piece crop (element list is not ordered by role).")
        return 1
    print("AGREE — detection is corroborated. If verify still fails, the fault")
    print("is in the behavioural payload, not the matcher; diff the payload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
