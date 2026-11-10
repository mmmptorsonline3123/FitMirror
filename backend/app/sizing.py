"""Size advice: body measurements vs a garment's size chart (body-measurement charts, the common retail kind)."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator

Measure = Literal["chest", "waist", "hip", "inseam", "shoulder_width"]
RELEVANT = {
    "top": ("chest", "waist", "shoulder_width"),
    "outerwear": ("chest", "waist", "shoulder_width"),
    "dress": ("chest", "waist", "hip"),
    "bottom": ("waist", "hip", "inseam"),
}
POINT_TOL_CM = 2.0  # a single chart value v is read as the range [v-2, v+2]
LABELS = {"chest": "chest", "waist": "waist", "hip": "hip", "inseam": "inseam", "shoulder_width": "shoulders"}


class SizeRow(BaseModel):
    label: str = Field(min_length=1, max_length=12)
    ranges: dict[Measure, tuple[float, float]]

    @field_validator("ranges", mode="before")
    @classmethod
    def points_to_ranges(cls, v):
        out = {}
        for k, r in v.items():
            lo, hi = (r - POINT_TOL_CM, r + POINT_TOL_CM) if isinstance(r, (int, float)) else r
            if not (0 < lo <= hi < 400):
                raise ValueError(f"bad range for {k}")
            out[k] = (lo, hi)
        return out


class SizeChart(BaseModel):
    unit: Literal["cm", "in"] = "cm"
    sizes: list[SizeRow] = Field(min_length=1, max_length=20)

    def in_cm(self) -> list[SizeRow]:
        f = 2.54 if self.unit == "in" else 1.0
        return [SizeRow(label=s.label, ranges={k: (lo * f, hi * f) for k, (lo, hi) in s.ranges.items()}) for s in self.sizes]


def _delta(value: float, lo: float, hi: float) -> float:
    """>0: body bigger than the size (tight). <0: body smaller (loose). 0: inside the range."""
    return value - hi if value > hi else value - lo if value < lo else 0.0


def advise(measurements: dict[str, dict], chart: SizeChart | None, category: str) -> dict:
    if chart is None:
        return {"recommended": None, "reason": "No size chart found for this garment.", "notes": [], "per_size": []}
    keys = RELEVANT.get(category, ("chest", "waist", "hip"))
    per_size = []
    for row in chart.in_cm():
        used = [k for k in keys if k in row.ranges and k in measurements]
        if not used:
            continue
        deltas = {k: _delta(measurements[k]["value_cm"], *row.ranges[k]) for k in used}
        tight = sum(max(d, 0) for d in deltas.values())
        loose = sum(max(-d, 0) for d in deltas.values())
        per_size.append({"label": row.label, "deltas": deltas, "score": 2 * tight + loose})  # tight is worse than loose
    if not per_size:
        return {"recommended": None, "reason": "The size chart has none of the measurements we can compare for this garment.",
                "notes": [], "per_size": []}
    best = min(per_size, key=lambda p: p["score"])
    notes = []
    for k, d in best["deltas"].items():
        pm = measurements[k]["pm_cm"]
        where = LABELS[k]
        if abs(d) <= pm and d != 0:
            notes.append(f"Borderline at the {where} ({d:+.0f} cm vs the size range, within our ±{pm:g} cm uncertainty).")
        elif d > 0:
            notes.append(f"Tight at the {where} (about {d:.0f} cm over this size's range).")
        elif d < 0:
            notes.append(f"Loose at the {where} (about {-d:.0f} cm under this size's range).")
        else:
            notes.append(f"Fits at the {where}.")
    worst_conf = max(measurements[k]["pm_cm"] for k in best["deltas"])
    confidence = "high" if best["score"] == 0 and worst_conf <= 3.5 else "medium" if best["score"] <= 6 else "low"
    return {"recommended": best["label"], "confidence": confidence, "notes": notes, "reason": None,
            "per_size": [{"label": p["label"], "deltas": {k: round(v, 1) for k, v in p["deltas"].items()}} for p in per_size]}
