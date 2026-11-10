import pytest
from pydantic import ValidationError
from app.sizing import SizeChart, advise
from .test_body import fit_ready_client
from .test_tryon import ready_garment

M = {"chest": {"value_cm": 96, "pm_cm": 3.5}, "waist": {"value_cm": 80, "pm_cm": 3.5},
     "hip": {"value_cm": 104, "pm_cm": 3.5}, "inseam": {"value_cm": 78, "pm_cm": 2.5}, "shoulder_width": {"value_cm": 42, "pm_cm": 2.5}}
CHART = {"unit": "cm", "sizes": [
    {"label": "S", "ranges": {"chest": [84, 90], "waist": [68, 74], "hip": [90, 96]}},
    {"label": "M", "ranges": {"chest": [91, 98], "waist": [75, 82], "hip": [97, 101]}},
    {"label": "L", "ranges": {"chest": [99, 106], "waist": [83, 90], "hip": [102, 109]}},
]}


def test_no_chart_is_honest():
    a = advise(M, None, "dress")
    assert a["recommended"] is None and a["reason"] == "No size chart found for this garment."


def test_dress_picks_m_with_borderline_hip():
    a = advise(M, SizeChart.model_validate(CHART), "dress")
    assert a["recommended"] == "M"  # hip +3 cm is inside our +-3.5 cm uncertainty -> borderline, not tight
    assert any("Borderline at the hip" in n for n in a["notes"]) and any("Fits at the chest" in n for n in a["notes"])


def test_tight_and_loose_notes():
    only_s = SizeChart.model_validate({"sizes": [CHART["sizes"][0]]})
    notes = advise(M, only_s, "dress")["notes"]
    assert any("Tight at the chest (about 6 cm" in n for n in notes) and any("Tight at the hip" in n for n in notes)
    only_l = SizeChart.model_validate({"sizes": [{"label": "XL", "ranges": {"chest": [110, 116]}}]})
    assert "Loose at the chest (about 14 cm" in advise(M, only_l, "top")["notes"][0]


def test_top_ignores_hip_and_fits_m():
    a = advise(M, SizeChart.model_validate(CHART), "top")
    assert a["recommended"] == "M" and not any("hip" in n for n in a["notes"]) and a["confidence"] == "high"


def test_bottom_prefers_larger_when_tight():
    a = advise(M, SizeChart.model_validate(CHART), "bottom")
    assert a["recommended"] == "L"  # M is tight at hip (+3); L is only loose at waist (-3) and tight is weighted 2x


def test_inches_and_point_values():
    chart = SizeChart.model_validate({"unit": "in", "sizes": [{"label": "32", "ranges": {"waist": 31.5}}, {"label": "34", "ranges": {"waist": 33.5}}]})
    a = advise(M, chart, "bottom")  # 80 cm = 31.5 in
    assert a["recommended"] == "32" and "Fits at the waist" in a["notes"][0]


def test_borderline_note():
    m = {**M, "chest": {"value_cm": 99.5, "pm_cm": 3.5}}
    a = advise(m, SizeChart.model_validate(CHART), "top")
    assert any("Borderline" in n for n in a["notes"])


def test_chart_validation():
    with pytest.raises(ValidationError):
        SizeChart.model_validate({"sizes": [{"label": "S", "ranges": {"chest": [100, 90]}}]})
    with pytest.raises(ValidationError):
        SizeChart.model_validate({"sizes": [{"label": "S", "ranges": {"neck": [30, 32]}}]})


def test_size_advice_endpoint():
    c = fit_ready_client(("front", "side"))
    g = ready_garment(c, category="dress")
    assert "body model" in c.get(f"/garments/{g['id']}/size-advice").json()["detail"]
    c.post("/body/fit")
    a = c.get(f"/garments/{g['id']}/size-advice").json()
    assert a["recommended"] is None and "No size chart" in a["reason"]
    r = c.patch(f"/garments/{g['id']}", json={"size_chart": CHART})
    assert r.status_code == 200 and r.json()["size_chart"]["sizes"][0]["label"] == "S" and r.json()["category"] == "dress"
    a = c.get(f"/garments/{g['id']}/size-advice").json()
    assert a["recommended"] in {"S", "M", "L"} and a["notes"] and set(a["measurements"]) >= {"chest", "waist", "hip"}
    assert c.patch(f"/garments/{g['id']}", json={"size_chart": None}).json()["size_chart"] is None
    assert c.patch(f"/garments/{g['id']}", json={"size_chart": {"sizes": []}}).status_code == 422
