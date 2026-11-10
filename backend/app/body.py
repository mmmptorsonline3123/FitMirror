"""Body model behind an interface (swappable for SMPL-X once its license + model files are in place).

Default: SilhouetteModel — license-free, CPU-only. It measures from person masks + 2D keypoints of the front/side
(and optional back) photos, scaled by the user's height, and builds a lofted elliptical-cross-section mesh.
It is an approximation: arms are not modelled and torso sections are treated as ellipses.
"""
import json
import math
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol
import numpy as np
from .config import get_settings
from .pose import Landmark

NOSE, LSH, RSH, LHIP, RHIP = 0, 11, 12, 23, 24
MEASURES = ("chest", "waist", "hip", "inseam", "shoulder_width")


class BodyFitError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ViewInput:
    landmarks: list[Landmark]
    mask: np.ndarray  # bool (h, w)


@dataclass
class BodyFit:
    measurements: dict[str, dict]  # name -> {"value_cm": float, "pm_cm": float}
    glb: bytes
    notes: list[str] = field(default_factory=list)
    profile: dict | None = None  # rings + key heights (cm), used to fit template garments (P5)


class BodyModel(Protocol):
    name: str
    def fit(self, views: dict[str, ViewInput], height_cm: float) -> BodyFit: ...


# ---------- geometry helpers ----------

def runs(row: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True segments [start, end) of a 1-D bool array."""
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def run_at(row: np.ndarray, x: float) -> tuple[int, int] | None:
    rs = runs(row)
    if not rs:
        return None
    return min(rs, key=lambda r: 0 if r[0] <= x < r[1] else min(abs(r[0] - x), abs(r[1] - x)))


def ellipse_circumference(width: float, depth: float) -> float:
    a, b = width / 2, depth / 2  # Ramanujan's approximation
    return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


class Calibrated:
    """One photo: mask + landmarks with a cm scale from the user's height (head top -> floor)."""

    def __init__(self, v: ViewInput, height_cm: float):
        self.mask = v.mask
        h, w = v.mask.shape
        rows = np.flatnonzero(v.mask.any(axis=1))
        if rows.size == 0:
            raise BodyFitError("No body silhouette found in a photo.")
        self.top, self.floor = int(rows[0]), int(rows[-1])
        self.ppcm = (self.floor - self.top) / height_cm
        if self.ppcm < 1.5:
            raise BodyFitError("Body is too small in a photo to measure. Retake it closer.")
        px = lambda i: (v.landmarks[i].x * w, v.landmarks[i].y * h)  # noqa: E731
        self.sh = [(px(LSH)[k] + px(RSH)[k]) / 2 for k in (0, 1)]
        self.hip = [(px(LHIP)[k] + px(RHIP)[k]) / 2 for k in (0, 1)]
        self.cx = (self.sh[0] + self.hip[0]) / 2
        self.shoulder_px = math.dist(px(LSH), px(RSH))
        self.facing = 1 if px(NOSE)[0] >= self.cx else -1  # side view: which way is "front"

    def h_of(self, y: float) -> float:
        return (self.floor - y) / self.ppcm

    def row_of(self, h_cm: float) -> int:
        return int(round(self.floor - h_cm * self.ppcm))

    def span(self, h_cm: float, band_cm: float = 1.0) -> tuple[float, float] | None:
        """(center_offset_cm, width_cm) of the body run around cx at height h_cm, median over a +-band."""
        out = []
        for r in range(self.row_of(h_cm + band_cm), self.row_of(h_cm - band_cm) + 1):
            if 0 <= r < self.mask.shape[0] and (seg := run_at(self.mask[r], self.cx)):
                out.append(((seg[0] + seg[1]) / 2, seg[1] - seg[0]))
        if not out:
            return None
        c, wd = np.median(np.array(out), axis=0)
        return (c - self.cx) / self.ppcm, wd / self.ppcm


# ---------- GLB writer (positions + indices; client computes normals) ----------

def write_glb(vertices: np.ndarray, faces: np.ndarray, uvs: np.ndarray | None = None, texture_jpeg: bytes | None = None,
              color=(0.78, 0.72, 0.66, 1), name: str = "body") -> bytes:
    """Minimal glTF 2.0 binary: positions + indices, optional TEXCOORD_0 + embedded JPEG base-colour texture."""
    chunks, views, accessors = [], [], []

    def add(data: bytes, target: int | None = None) -> int:
        offset = sum(len(c) for c in chunks)
        chunks.append(data + b"\0" * (-len(data) % 4))  # keep every bufferView 4-byte aligned
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), **({"target": target} if target else {})})
        return len(views) - 1

    accessors.append({"bufferView": add(vertices.astype("<f4").tobytes(), 34962), "componentType": 5126, "count": len(vertices),
                      "type": "VEC3", "min": vertices.min(0).tolist(), "max": vertices.max(0).tolist()})
    attrs = {"POSITION": 0}
    if uvs is not None:
        accessors.append({"bufferView": add(uvs.astype("<f4").tobytes(), 34962), "componentType": 5126, "count": len(uvs), "type": "VEC2"})
        attrs["TEXCOORD_0"] = len(accessors) - 1
    accessors.append({"bufferView": add(faces.astype("<u4").ravel().tobytes(), 34963), "componentType": 5125,
                      "count": faces.size, "type": "SCALAR"})
    pbr = {"baseColorFactor": list(color), "metallicFactor": 0, "roughnessFactor": 0.8}
    gltf = {
        "asset": {"version": "2.0", "generator": "fitmirror"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"primitives": [{"attributes": attrs, "indices": len(accessors) - 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": pbr, "doubleSided": True}],
        "accessors": accessors,
    }
    if texture_jpeg is not None:
        gltf["images"] = [{"bufferView": add(texture_jpeg), "mimeType": "image/jpeg"}]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]
        gltf["textures"] = [{"source": 0, "sampler": 0}]
        pbr["baseColorTexture"] = {"index": 0}
        pbr["baseColorFactor"] = [1, 1, 1, 1]
    binary = b"".join(chunks)
    gltf["buffers"] = [{"byteLength": len(binary)}]
    gltf["bufferViews"] = views
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def loft(rings: list[tuple[float, float, float, float, float]], seg: int = 32, caps: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """rings: (y_cm, x_cm, z_cm, width_cm, depth_cm) bottom->top. Returns (vertices m, faces); caps=False leaves open tubes."""
    t = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    verts = [np.stack([x + w / 2 * np.cos(t), np.full(seg, y), z + d / 2 * np.sin(t)], 1) for y, x, z, w, d in rings]
    v = np.concatenate(verts + [np.array([[rings[0][1], rings[0][0], rings[0][2]], [rings[-1][1], rings[-1][0], rings[-1][2]]])])
    faces = []
    for i in range(len(rings) - 1):
        for j in range(seg):
            a, b = i * seg + j, i * seg + (j + 1) % seg
            faces += [(a, a + seg, b), (b, a + seg, b + seg)]
    if not caps:
        return v[:-2] / 100.0, np.array(faces, dtype=np.uint32)
    bot, top, last = len(v) - 2, len(v) - 1, (len(rings) - 1) * seg
    faces += [(bot, (j + 1) % seg, j) for j in range(seg)] + [(top, last + j, last + (j + 1) % seg) for j in range(seg)]
    return v / 100.0, np.array(faces, dtype=np.uint32)


# ---------- the default model ----------

class SilhouetteModel:
    name = "silhouette-v1"
    BASE_PM = {"chest": 3.5, "waist": 3.5, "hip": 3.5, "inseam": 2.5, "shoulder_width": 2.5}

    def fit(self, views, height_cm):
        if "front" not in views or "side" not in views:
            raise BodyFitError("Front and side photos are both needed to build your body model.")
        f, s = Calibrated(views["front"], height_cm), Calibrated(views["side"], height_cm)
        b = Calibrated(views["back"], height_cm) if "back" in views else None
        notes = []
        sh_h, hip_h = f.h_of(f.sh[1]), f.h_of(f.hip[1])
        torso = sh_h - hip_h
        if torso < 0.15 * height_cm:
            raise BodyFitError("Couldn't locate shoulders and hips reliably. Retake the front photo standing straight.")

        def girth(h_cm):
            fw, sd = f.span(h_cm), s.span(h_cm)
            if not fw or not sd:
                raise BodyFitError("Couldn't trace your outline at every level. Retake photos against a plain background.")
            c = ellipse_circumference(fw[1], sd[1])
            if b and (bw := b.span(h_cm)):
                cb = ellipse_circumference(bw[1], sd[1])
                return (c + cb) / 2, abs(c - cb) / 2
            return c, 1.0

        chest_h = sh_h - 0.28 * torso
        waist_h = min((sh_h - k * torso for k in np.linspace(0.45, 0.85, 17)), key=lambda h: (f.span(h) or (0, 1e9))[1])
        hip_h_meas = hip_h - 0.10 * torso
        m = {}
        for name, h in (("chest", chest_h), ("waist", waist_h), ("hip", hip_h_meas)):
            c, extra = girth(h)
            m[name] = (c, self.BASE_PM[name] + extra)

        crotch_h = None
        for r in range(f.row_of(hip_h), f.floor):
            if not f.mask[r, int(f.cx)]:
                crotch_h = f.h_of(r)
                break
        if crotch_h is None or crotch_h < 0.3 * height_cm:
            crotch_h = 0.45 * height_cm  # anthropometric fallback when legs touch
            m["inseam"] = (crotch_h, 6.0)
            notes.append("Legs were touching in the front photo, so inseam is estimated from height.")
        else:
            m["inseam"] = (crotch_h, self.BASE_PM["inseam"])
        m["shoulder_width"] = (f.shoulder_px / f.ppcm * 1.12, self.BASE_PM["shoulder_width"])  # joint centres -> acromion
        if not b:
            notes.append("Add a back photo to tighten the confidence ranges.")
        measurements = {k: {"value_cm": round(v, 1), "pm_cm": round(pm * 2) / 2} for k, (v, pm) in m.items()}

        # Mesh: torso+head loft from crotch to head top; one loft per leg below the crotch.
        def rings(h_from, h_to, pick):
            out = []
            for h in np.arange(h_from, h_to, 1.0):
                fr, sd = pick(f, h), s.span(h)
                if fr and sd:
                    out.append((h, fr[0], s.facing * sd[0], max(fr[1], 0.5), max(sd[1], 0.5)))
            return out

        def leg(side):
            def pick(cal, h):
                r = cal.row_of(h)
                rs = [x for x in runs(cal.mask[r])] if 0 <= r < cal.mask.shape[0] else []
                rs = [x for x in rs if (x[0] + x[1]) / 2 < cal.cx] if side < 0 else [x for x in rs if (x[0] + x[1]) / 2 >= cal.cx]
                if not rs:
                    return None
                x = max(rs, key=lambda q: q[0]) if side < 0 else min(rs, key=lambda q: q[0])  # innermost run = leg, not arm/hand
                return ((x[0] + x[1]) / 2 - cal.cx) / cal.ppcm, (x[1] - x[0]) / cal.ppcm
            return pick

        # Torso starts just above the crotch and legs stop just below it: on the crotch row itself the centre column is
        # background, so "run nearest the centre" would snap the torso's bottom ring onto one leg (and a leg's top ring onto the torso).
        parts = [rings(crotch_h + 1.5, height_cm - 0.5, lambda cal, h: cal.span(h, 0.5)),
                 rings(0.5, crotch_h - 1, leg(-1)), rings(0.5, crotch_h - 1, leg(1))]
        vs, fs, off = [], [], 0
        for rs in parts:
            if len(rs) >= 2:
                v, fc = loft(rs)
                vs.append(v)
                fs.append(fc + off)
                off += len(v)
        if not vs:
            raise BodyFitError("Couldn't build a mesh from the photos.")
        r = lambda rs: [[round(float(c), 2) for c in ring] for ring in rs]  # noqa: E731
        profile = {"height_cm": height_cm, "torso": r(parts[0]), "legs": [r(parts[1]), r(parts[2])],
                   **{k: round(float(v), 2) for k, v in (("shoulder_h", sh_h), ("chest_h", chest_h), ("waist_h", waist_h),
                                                         ("hip_h", hip_h), ("crotch_h", crotch_h))}}
        return BodyFit(measurements, write_glb(np.concatenate(vs), np.concatenate(fs)), notes, profile)


@lru_cache
def get_body_model() -> BodyModel:
    name = get_settings().body_model
    if name == "silhouette":
        return SilhouetteModel()
    # shortcut: SMPL-X needs licensed model files (non-commercial) + a GPU fitter via InferenceProvider; add it here.
    raise RuntimeError(f"BODY_MODEL={name!r} is not available (SMPL-X requires licensed model files; see README)")
