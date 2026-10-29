"""Generate synthetic top-down potato canopy photos for the offline demo and the tests.

Writes examples/images/<ZONE>_<n>.jpg for the six zones in field_demo.json. The photos are
synthetic and deterministic (fixed seeds): green leaf blobs on brown soil, with brown spots
*enclosed* by leaf tissue where a zone is meant to be diseased. Soil between the plants is the
same hue as the spots, which is exactly the case the lesion index in perception.py has to get right.

    python examples/make_demo_images.py
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage as ndi

OUT = Path(__file__).resolve().parent / "images"
W, H = 960, 720

# zone -> (target canopy cover, enclosed lesion spots per photo, photos)
ZONES = {
    "A1": (0.38, 0, 2),     # healthy
    "A2": (0.42, 190, 2),   # severe blight: stub reports severity 3
    "A3": (0.36, 0, 2),     # healthy
    "B1": (0.34, 30, 2),    # weak signal: must go to a human
    "B2": (0.40, 170, 2),   # blight: treated
    "B3": (0.44, 0, 2),     # healthy
}


def _leaf(draw: ImageDraw.ImageDraw, cx: float, cy: float, a: float, b: float, ang: float) -> None:
    pts = []
    for k in range(28):
        t = 2 * math.pi * k / 28
        x, y = a * math.cos(t), b * math.sin(t) * (1 + 0.25 * math.cos(t))   # slightly pointed tip
        pts.append((cx + x * math.cos(ang) - y * math.sin(ang), cy + x * math.sin(ang) + y * math.cos(ang)))
    draw.polygon(pts, fill=255)


def render(seed: int, cover: float, spots: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    mask_im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask_im)
    # plants: clusters of leaves around centres, until the target cover is reached
    centres = [(rng.uniform(80, W - 80), rng.uniform(70, H - 70)) for _ in range(14)]
    while np.asarray(mask_im).mean() / 255.0 < cover:
        cx, cy = centres[rng.integers(len(centres))]
        r = rng.uniform(0, 95)
        t = rng.uniform(0, 2 * math.pi)
        _leaf(d, cx + r * math.cos(t), cy + r * math.sin(t), rng.uniform(28, 52), rng.uniform(14, 26),
              rng.uniform(0, math.pi))
    leaf = np.asarray(mask_im) > 127

    # soil: brown with low-frequency + fine noise
    soil = np.zeros((H, W, 3), np.float32)
    soil[:] = (122, 88, 58)
    soil += ndi.gaussian_filter(rng.normal(0, 1, (H, W)), 25)[..., None] * np.array([90, 70, 50])
    soil += rng.normal(0, 7, (H, W, 1))
    img = soil.copy()

    # leaves: green with per-pixel variation
    green = np.zeros((H, W, 3), np.float32)
    green[:] = (52, 128, 44)
    green += ndi.gaussian_filter(rng.normal(0, 1, (H, W)), 8)[..., None] * np.array([30, 45, 25])
    green += rng.normal(0, 3, (H, W, 1))
    img[leaf] = green[leaf]

    # enclosed lesions: brown spots wholly inside leaf tissue
    if spots:
        inner = ndi.binary_erosion(leaf, iterations=11)
        ys, xs = np.nonzero(inner)
        if len(ys):
            sp = Image.new("L", (W, H), 0)
            sd = ImageDraw.Draw(sp)
            for i in rng.choice(len(ys), size=min(spots, len(ys)), replace=False):
                rad = rng.uniform(3.5, 7.0)
                sd.ellipse((xs[i] - rad, ys[i] - rad, xs[i] + rad, ys[i] + rad), fill=255)
            spot = (np.asarray(sp) > 127) & ndi.binary_erosion(leaf, iterations=3)
            img[spot] = np.array((108, 66, 34)) + rng.normal(0, 5, (int(spot.sum()), 3))

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(0.8))


def _lesion(jpg: bytes) -> float:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fieldpilot.perception import pixel_metrics
    return pixel_metrics(jpg)["lesion_index_pct"]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.jpg"):
        old.unlink()
    for zi, (zone, (cover, spots, n)) in enumerate(ZONES.items()):
        for k in range(n):
            seed = 1000 * (zi + 1) + k
            for attempt in range(40):
                im = render(seed + 100 * attempt, cover, spots)
                if spots:
                    break
                # a healthy photo must not contain an accidental enclosed soil pocket
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=90)
                if _lesion(buf.getvalue()) < 0.4:
                    break
            im.save(OUT / f"{zone}_{k + 1}.jpg", quality=90)
    print(f"wrote {sum(v[2] for v in ZONES.values())} photos to {OUT}")


if __name__ == "__main__":
    main()
