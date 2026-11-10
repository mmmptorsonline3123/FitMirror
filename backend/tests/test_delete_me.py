from fastapi.testclient import TestClient
from app.db import USER_COLLECTIONS, get_db
from app.main import app
from .conftest import make_client, upload


def test_delete_me_purges_everything_and_only_mine(s3):
    a, b = make_client("a@example.com"), make_client("b@example.com")
    a_id, b_id = a.get("/me").json()["id"], b.get("/me").json()["id"]
    for v in ("front", "side", "back"):
        assert upload(a, v).status_code == 201
    upload(b, "front")
    from .test_tryon import ready_garment
    g = ready_garment(a, category="top")
    t = a.post("/tryon", json={"garment_id": g["id"]}).json()
    assert a.get(f"/tryon/{t['id']}").json()["status"] == "done"
    db = get_db()
    assert db.garments.count_documents({"user_id": a_id}) == 1 and db.tryons.count_documents({"user_id": a_id}) == 1

    r = a.delete("/me")
    assert r.status_code == 204 and "fm_session=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test").get("Contents", [])]
    assert not [k for k in keys if a_id in k]
    assert len([k for k in keys if b_id in k]) == 1
    for name in USER_COLLECTIONS:
        assert db[name].count_documents({"user_id": a_id}) == 0
    assert db.users.count_documents({"email": "a@example.com"}) == 0
    assert db.photos.count_documents({"user_id": b_id}) == 1

    c = TestClient(app)
    assert c.post("/auth/login", json={"email": "a@example.com", "password": "correct-horse-battery"}).status_code == 401
    assert a.get("/me").status_code == 401
    assert b.get("/me").status_code == 200


def test_delete_me_requires_auth():
    assert TestClient(app).delete("/me").status_code == 401
