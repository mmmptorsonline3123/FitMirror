"""P5 (feature-flagged) 3D garment PREVIEW: a per-category template garment lofted around the user's body profile,
with the garment cut-out projected onto it from the front. Not a cloth simulation — no drape, no sleeves, back is a
mirrored projection of the front. Generated procedurally, so there are no third-party garment assets or licenses."""
import io
import numpy as np
from PIL import Image, ImageOps
from .body import loft, write_glb

# (width multiplier, extra cm) ease over the body, per category
EASE = {"top": (1.06, 2.0), "outerwear": (1.12, 4.0), "dress": (1.05, 1.5), "bottom": (1.04, 1.5)}


def _eased(ring, k, add):
    y, x, z, w, d = ring
    return (y, x, z, w * k + add, d * k + add)


def _between(rings, lo, hi):
    return [r for r in rings if lo <= r[0] <= hi]


def _ring_at(rings, h):
    return min(rings, key=lambda r: abs(r[0] - h))


def _skirt(p, k, add, hem_h):
    """Flared tube from the crotch down to hem_h, always wider than both legs together."""
    top = _eased(p["torso"][0], k, add)  # lowest torso ring = just above the crotch
    out = []
    for h in np.arange(hem_h, top[0], 2.0):
        t = (top[0] - h) / max(top[0] - hem_h, 1)  # 0 at crotch -> 1 at hem
        l, r = _ring_at(p["legs"][0], h), _ring_at(p["legs"][1], h)
        span = (r[1] + r[3] / 2) - (l[1] - l[3] / 2)
        w = max(top[3] * (1 + 0.35 * t), span * 1.08 + 2)
        d = max(top[4] * (1 + 0.25 * t), max(l[4], r[4]) * 1.1 + 2)
        out.append((float(h), top[1], top[2], w, d))
    return out


def garment_lofts(p: dict, category: str) -> list[list[tuple]]:
    """Ring lists (bottom->top, cm) for the template garment of `category` fitted to body profile `p`."""
    k, add = EASE[category]
    torso = p["shoulder_h"] - p["hip_h"]
    H = p["height_cm"]
    if category == "top":
        return [[_eased(r, k, add) for r in _between(p["torso"], p["hip_h"] - 0.15 * torso, p["shoulder_h"] - 0.02 * torso)]]
    if category == "outerwear":
        return [[_eased(r, k, add) for r in _between(p["torso"], p["hip_h"] - 0.35 * torso, p["shoulder_h"] + 0.05 * torso)]]
    if category == "dress":
        upper = [_eased(r, k, add) for r in _between(p["torso"], p["crotch_h"], p["shoulder_h"] - 0.02 * torso)]
        return [_skirt(p, k, add, hem_h=0.30 * H) + upper]
    if category == "bottom":
        waist = [_eased(r, k, add) for r in _between(p["torso"], p["crotch_h"], p["waist_h"])]
        legs = [[_eased(r, 1.12, 2.0) for r in _between(leg, 0.06 * H, p["crotch_h"] + 1)] for leg in p["legs"]]
        return [waist, *legs]
    raise ValueError(category)


def texture_from_cutout(cutout_jpeg: bytes, max_side: int = 1024) -> bytes:
    """Crop the white padding around the cut-out so the garment fills the UV square."""
    img = Image.open(io.BytesIO(cutout_jpeg)).convert("RGB")
    bbox = ImageOps.invert(img.convert("L")).point(lambda v: 255 if v > 12 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def build_preview(profile: dict, category: str, cutout_jpeg: bytes) -> bytes:
    vs, fs, off = [], [], 0
    for rings in garment_lofts(profile, category):
        if len(rings) >= 2:
            v, f = loft(rings, caps=False)
            vs.append(v)
            fs.append(f + off)
            off += len(v)
    if not vs:
        raise ValueError("body profile too small for this garment")
    v = np.concatenate(vs)
    lo, hi = v.min(0), v.max(0)
    # Planar projection from the front: image left->right = x, image top->bottom = y (glTF UV origin is top-left).
    uv = np.stack([(v[:, 0] - lo[0]) / max(hi[0] - lo[0], 1e-6), (hi[1] - v[:, 1]) / max(hi[1] - lo[1], 1e-6)], 1)
    return write_glb(v, np.concatenate(fs), uvs=uv, texture_jpeg=texture_from_cutout(cutout_jpeg), name=f"garment-{category}-preview")
