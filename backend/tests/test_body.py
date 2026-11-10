import json
import math
import struct
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app.body import Calibrated, ViewInput, ellipse_circumference, loft, runs, write_glb
from app.main import app
from app.pose import FakeDetector
from .conftest import make_client, upload


def parse_glb(data: bytes):
    magic, version, total = struct.unpack_from("<4sII", data)
    assert magic == b"glTF" and version == 2 and total == len(data)
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    assert jtype == b"JSON"
    gltf = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack_from("<I4s", data, 20 + jlen)
    assert btype == b"BIN\0"
    binary = data[28 + jlen:28 + jlen + blen]
    acc, views = gltf["accessors"], gltf["bufferViews"]
    prim = gltf["meshes"][0]["primitives"][0]
    pa, ia = acc[prim["attributes"]["POSITION"]], acc[prim["indices"]]
    pos = np.frombuffer(binary, "<f4", pa["count"] * 3, offset=views[pa["bufferView"]]["byteOffset"]).reshape(-1, 3)
    idx = np.frombuffer(binary, "<u4", ia["count"], offset=views[ia["bufferView"]]["byteOffset"])
    assert np.allclose(pos.min(0), pa["min"]) and np.allclose(pos.max(0), pa["max"])
    return gltf, pos, idx


def test_geometry_helpers():
    assert runs(np.array([0, 1, 1, 0, 1, 0], bool)) == [(1, 3), (4, 5)]
    assert ellipse_circumference(20, 20) == pytest.approx(math.pi * 20, rel=1e-6)
    assert ellipse_circumference(32, 24) == pytest.approx(88.4, abs=0.2)


def test_glb_roundtrip_and_loft():
    v, f = loft([(0, 0, 0, 10, 10), (50, 0, 0, 10, 10), (100, 0, 0, 20, 10)])
    gltf, pos, idx = parse_glb(write_glb(v, f))
    assert pos.shape == (3 * 32 + 2, 3) and idx.max() < len(pos) and len(idx) % 3 == 0
    assert pos[:, 1].max() == pytest.approx(1.0) and gltf["accessors"][0]["max"][1] == pytest.approx(1.0)


def test_calibration_scale_from_height():
    mask = np.zeros((1000, 600), bool)
    mask[100:900, 250:350] = True  # 800 px tall body
    lm = FakeDetector().detect(np.random.default_rng(0).integers(0, 255, (1000, 600, 3), np.uint8))[0]
    cal = Calibrated(ViewInput(lm, mask), 160)
    assert cal.ppcm == pytest.approx(799 / 160)
    _, width = cal.span(80)
    assert width == pytest.approx(100 / cal.ppcm, rel=0.01)


def fit_ready_client(views=("front", "side", "back"), height=170):
    c = make_client("body@example.com")
    c.patch("/me", json={"height_cm": height})
    for v in views:
        assert upload(c, v).status_code == 201
    return c


def test_fit_body_end_to_end(s3):
    c = fit_ready_client()
    r = c.post("/body/fit")
    assert r.status_code == 202 and r.json()["status"] == "running"
    b = c.get(f"/body/{r.json()['id']}").json()  # TestClient ran the background task already
    assert b["status"] == "done", b
    m = b["measurements"]
    assert set(m) == {"chest", "waist", "hip", "inseam", "shoulder_width"}
    # Fake silhouette: plausible adult proportions for 170 cm, and waist narrower than chest and hip.
    assert 70 < m["chest"]["value_cm"] < 120 and 55 < m["waist"]["value_cm"] < 110 and 70 < m["hip"]["value_cm"] < 125
    assert m["waist"]["value_cm"] < m["chest"]["value_cm"] and m["waist"]["value_cm"] < m["hip"]["value_cm"]
    assert 65 < m["inseam"]["value_cm"] < 85 and 30 < m["shoulder_width"]["value_cm"] < 50
    assert all(v["pm_cm"] > 0 for v in m.values())
    assert b["views_used"] == ["back", "front", "side"] and b["notes"] == []
    assert c.get("/body/latest").json()["id"] == b["id"]

    mesh = c.get(b["mesh_url"])
    assert mesh.status_code == 200 and mesh.headers["content-type"] == "model/gltf-binary"
    assert "no-store" in mesh.headers["cache-control"]
    _, pos, _ = parse_glb(mesh.content)
    assert pos[:, 1].max() == pytest.approx(1.7, abs=0.02) and pos[:, 1].min() < 0.02  # metres, feet at 0


def test_without_back_photo_has_wider_confidence():
    c = fit_ready_client(("front", "side"))
    b = c.get(f"/body/{c.post('/body/fit').json()['id']}").json()
    assert b["status"] == "done" and any("back photo" in n for n in b["notes"])


def test_fit_preconditions():
    c = make_client("pre@example.com")
    assert "height" in c.post("/body/fit").json()["detail"]
    c.patch("/me", json={"height_cm": 170})
    upload(c, "front")
    assert "side" in c.post("/body/fit").json()["detail"]
    assert c.get("/body/latest").status_code == 404


def test_fit_failure_is_reported(monkeypatch):
    c = fit_ready_client(("front", "side"))
    monkeypatch.setattr(FakeDetector, "segment", lambda self, rgb: None)
    b = c.get(f"/body/{c.post('/body/fit').json()['id']}").json()
    assert b["status"] == "failed" and "couldn't find you" in b["error"] and b["mesh_url"] is None


def test_body_and_mesh_are_private():
    c = fit_ready_client(("front", "side"))
    bid = c.post("/body/fit").json()["id"]
    other = make_client("spy@example.com")
    assert other.get(f"/body/{bid}").status_code == 404
    assert other.get(f"/body/{bid}/mesh").status_code == 404
    assert TestClient(app).get(f"/body/{bid}/mesh").status_code == 401


def test_delete_me_removes_bodies(s3):
    c = fit_ready_client(("front", "side"))
    c.post("/body/fit")
    assert c.delete("/me").status_code == 204
    assert "Contents" not in s3.list_objects_v2(Bucket="fitmirror-test")
