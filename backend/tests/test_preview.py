import io
import struct
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.config import get_settings
from app.db import get_db
from app.main import app
from .conftest import make_client
from .test_body import fit_ready_client, parse_glb
from .test_tryon import ready_garment


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("FEATURE_3D_GARMENT", "true")
    get_settings.cache_clear()


def glb_parts(data: bytes):
    gltf, pos, idx = parse_glb(data)
    jlen = struct.unpack_from("<I", data, 12)[0]
    binary = data[28 + jlen:]
    views, acc = gltf["bufferViews"], gltf["accessors"]
    uv_acc = acc[gltf["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]]
    uv_view = views[uv_acc["bufferView"]]
    uv = np.frombuffer(binary, "<f4", uv_acc["count"] * 2, offset=uv_view["byteOffset"]).reshape(-1, 2)
    img_view = views[gltf["images"][0]["bufferView"]]
    img = Image.open(io.BytesIO(binary[img_view["byteOffset"]:img_view["byteOffset"] + img_view["byteLength"]]))
    assert all(v["byteOffset"] % 4 == 0 for v in views)
    return gltf, pos, idx, uv, img


def setup(category="top"):
    c = fit_ready_client(("front", "side", "back"))
    c.post("/body/fit")
    g = ready_garment(c, category=category)
    return c, g


def test_flag_off_hides_everything():
    c, g = setup()
    assert c.get("/features").json() == {"garment_3d": False}
    assert c.post("/tryon3d", json={"garment_id": g["id"]}).status_code == 404
    assert c.get("/tryon3d/whatever/mesh").status_code == 404


@pytest.mark.parametrize("category", ["top", "outerwear", "dress", "bottom"])
def test_preview_mesh_per_category(flag_on, category):
    c, g = setup(category)
    assert c.get("/features").json() == {"garment_3d": True}
    r = c.post("/tryon3d", json={"garment_id": g["id"]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["label"] == "preview" and p["category"] == category
    mesh = c.get(p["mesh_url"])
    assert mesh.status_code == 200 and mesh.headers["content-type"] == "model/gltf-binary"
    gltf, pos, idx, uv, img = glb_parts(mesh.content)
    assert idx.max() < len(pos) and len(idx) % 3 == 0
    assert uv.min() >= 0 and uv.max() <= 1 and img.format == "JPEG"
    assert gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}

    body = get_db().bodies.find_one({"_id": p["body_id"]})["profile"]
    y_lo, y_hi = pos[:, 1].min() * 100, pos[:, 1].max() * 100
    if category == "top":
        assert body["hip_h"] - 15 < y_lo < body["hip_h"] and y_hi <= body["shoulder_h"]
    elif category == "outerwear":
        assert y_lo < body["hip_h"] - 5 and y_hi > body["shoulder_h"]
    elif category == "dress":
        assert y_lo == pytest.approx(0.30 * body["height_cm"], abs=2) and y_hi <= body["shoulder_h"]
    else:
        assert y_lo < 0.1 * body["height_cm"] and y_hi == pytest.approx(body["waist_h"], abs=1.5)


def test_garment_is_bigger_than_body(flag_on):
    """Ease: at chest height the garment's ring is wider than the body's."""
    c, g = setup("top")
    p = c.post("/tryon3d", json={"garment_id": g["id"]}).json()
    _, pos, _ = parse_glb(c.get(p["mesh_url"]).content)
    b = get_db().bodies.find_one({"_id": p["body_id"]})
    _, body_pos, _ = parse_glb(c.get(f"/body/{b['_id']}/mesh").content)
    h = b["profile"]["chest_h"] / 100
    width = lambda v: np.ptp(v[np.abs(v[:, 1] - h) < 0.006][:, 0])  # noqa: E731
    assert width(pos) > width(body_pos) + 0.01


def test_preconditions(flag_on):
    c = make_client("pre3d@example.com")
    c.patch("/me", json={"height_cm": 170})
    g = ready_garment(c, category="top")
    assert "Build your 3D body" in c.post("/tryon3d", json={"garment_id": g["id"]}).json()["detail"]
    c2, g2 = setup()
    get_db().bodies.update_many({}, {"$unset": {"profile": ""}})  # bodies fitted before P5
    assert "Rebuild" in c2.post("/tryon3d", json={"garment_id": g2["id"]}).json()["detail"]


def test_privacy_and_delete(flag_on, s3):
    c, g = setup()
    p = c.post("/tryon3d", json={"garment_id": g["id"]}).json()
    spy = make_client("spy3d@example.com")
    assert spy.get(p["mesh_url"]).status_code == 404
    assert spy.post("/tryon3d", json={"garment_id": g["id"]}).status_code == 404
    assert TestClient(app).get(p["mesh_url"]).status_code == 401
    assert c.delete("/me").status_code == 204
    assert get_db().previews3d.count_documents({}) == 0
    assert "Contents" not in s3.list_objects_v2(Bucket="fitmirror-test")


def test_profile_crotch_boundary_regression():
    """Torso rings must not snap onto a leg at the crotch row (and leg rings must not grab the torso)."""
    c, _ = setup()
    p = get_db().bodies.find_one({})["profile"]
    bottom = p["torso"][0]
    assert abs(bottom[1]) < 2 and bottom[3] > max(r[3] for leg in p["legs"] for r in leg)
    assert all(r[1] < 0 for r in p["legs"][0]) and all(r[1] > 0 for r in p["legs"][1])


def test_dress_skirt_is_centred_and_flared():
    from app.garment3d import garment_lofts
    c, _ = setup()
    p = get_db().bodies.find_one({})["profile"]
    (dress,) = garment_lofts(p, "dress")
    skirt = [r for r in dress if r[0] < p["crotch_h"]]
    assert all(abs(r[1]) < 2 for r in skirt)
    assert skirt[0][3] > skirt[-1][3] > p["torso"][0][3]  # hem widest, still wider than the hips
