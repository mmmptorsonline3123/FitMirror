import pytest
from fastapi.testclient import TestClient
from app.config import get_settings
from app.db import get_db
from app.main import app
from .conftest import make_client, upload
from .test_tryon import ready_garment


def keys(s3):
    return [o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test").get("Contents", [])]


def done_tryon(c, g):
    t = c.post("/tryon", json={"garment_id": g["id"]}).json()
    assert c.get(f"/tryon/{t['id']}").json()["status"] == "done"
    return t


def test_wardrobe_lists_garments_and_tryons_newest_first(client):
    upload(client, "front")
    g1 = ready_garment(client, category="top")
    g2 = ready_garment(client, category="dress")
    t1, t2 = done_tryon(client, g1), done_tryon(client, g2)
    w = client.get("/wardrobe").json()
    assert [g["id"] for g in w["garments"]] == [g2["id"], g1["id"]]
    assert [t["id"] for t in w["tryons"]] == [t2["id"], t1["id"]]
    assert w["tryons"][0]["garment_id"] == g2["id"] and w["tryons"][0]["views"]["front"]["result_url"]
    assert make_client("empty@example.com").get("/wardrobe").json() == {"garments": [], "tryons": []}
    assert TestClient(app).get("/wardrobe").status_code == 401


def test_wardrobe_advances_running_jobs(client):
    upload(client, "front")
    g = ready_garment(client, category="top")
    client.post("/tryon", json={"garment_id": g["id"]})
    assert client.get("/wardrobe").json()["tryons"][0]["status"] == "done"  # fake provider: second poll succeeds


def test_delete_tryon(client, s3):
    upload(client, "front")
    g = ready_garment(client, category="top")
    t1, t2 = done_tryon(client, g), done_tryon(client, g)
    assert client.delete(f"/tryon/{t1['id']}").status_code == 204
    assert not [k for k in keys(s3) if f"/tryons/{t1['id']}/" in k]
    assert [k for k in keys(s3) if f"/tryons/{t2['id']}/" in k]
    assert [t["id"] for t in client.get("/wardrobe").json()["tryons"]] == [t2["id"]]
    assert client.get(f"/tryon/{t1['id']}").status_code == 404
    assert client.delete(f"/tryon/{t1['id']}").status_code == 404


def test_delete_garment_cascades(client, s3, monkeypatch):
    monkeypatch.setenv("FEATURE_3D_GARMENT", "true")
    get_settings.cache_clear()
    for v in ("front", "side"):
        upload(client, v)
    client.patch("/me", json={"height_cm": 170})
    client.post("/body/fit")
    g, keep = ready_garment(client, category="top"), ready_garment(client, category="dress")
    t = done_tryon(client, g)
    kept_t = done_tryon(client, keep)
    p = client.post("/tryon3d", json={"garment_id": g["id"]}).json()

    assert client.delete(f"/garments/{g['id']}").status_code == 204
    left = keys(s3)
    assert not [k for k in left if f"/garments/{g['id']}/" in k or f"/tryons/{t['id']}/" in k or p["id"] in k]
    assert [k for k in left if f"/garments/{keep['id']}/" in k] and [k for k in left if f"/tryons/{kept_t['id']}/" in k]
    db = get_db()
    assert db.tryons.count_documents({"garment_id": g["id"]}) == 0 and db.previews3d.count_documents({"garment_id": g["id"]}) == 0
    w = client.get("/wardrobe").json()
    assert [x["id"] for x in w["garments"]] == [keep["id"]] and [x["id"] for x in w["tryons"]] == [kept_t["id"]]
    assert any("/photos/" in k for k in left) and any("/bodies/" in k for k in left)  # photos/body untouched


@pytest.mark.parametrize("method,path", [("delete", "/garments/{g}"), ("delete", "/tryon/{t}")])
def test_cannot_delete_other_users_items(client, method, path):
    upload(client, "front")
    g = ready_garment(client, category="top")
    t = done_tryon(client, g)
    spy = make_client("spy-w@example.com")
    assert getattr(spy, method)(path.format(g=g["id"], t=t["id"])).status_code == 404
    assert len(client.get("/wardrobe").json()["tryons"]) == 1
