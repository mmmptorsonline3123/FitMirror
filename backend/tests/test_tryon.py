import io
import numpy as np
from PIL import Image
from app.inference import get_provider
from .conftest import jpeg, make_client, upload


def garment_jpeg(w=500, h=600):
    arr = np.full((h, w, 3), 255, np.uint8)
    arr[150:450, 125:375] = (30, 60, 200)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG")
    return buf.getvalue()


def add_garment(c, category=None, data=None):
    form = {"category": category} if category else {}
    return c.post("/garments/from-image", data=form, files={"file": ("g.jpg", data or garment_jpeg(), "image/jpeg")})


def ready_garment(c, **kw):
    r = add_garment(c, **kw)
    assert r.status_code == 202, r.text
    gid = r.json()["id"]
    for _ in range(5):
        g = c.get(f"/garments/{gid}").json()
        if g["status"] != "processing":
            return g
    raise AssertionError(g)


def test_garment_auto_classify_and_segment(client, s3):
    r = add_garment(client)
    assert r.json()["status"] == "processing" and r.json()["category"] is None
    g = ready_garment(client)
    assert g["status"] == "ready" and g["category"] == "top" and g["category_source"] == "auto" and g["note"] is None
    assert g["image_url"] != g["original_url"] and "X-Amz-Signature" in g["image_url"]
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test")["Contents"]]
    assert any(k.endswith("/cutout.jpg") for k in keys) and any(k.endswith("/original.jpg") for k in keys)
    assert len(client.get("/garments").json()) == 2


def test_cutout_is_cropped_and_white_backed(client, s3):
    ready_garment(client, category="dress")
    key = next(o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test")["Contents"] if o["Key"].endswith("cutout.jpg"))
    img = Image.open(io.BytesIO(s3.get_object(Bucket="fitmirror-test", Key=key)["Body"].read())).convert("RGB")
    assert abs(img.width / img.height - 0.75) < 0.02  # padded to 3:4
    assert img.getpixel((2, 2))[0] > 240  # white border


def test_user_category_skips_classifier(client):
    g = ready_garment(client, category="bottom")
    assert g["category"] == "bottom" and g["category_source"] == "user"


def test_classifier_failure_asks_user_then_patch(client):
    get_provider().fail_next = "boom"
    g = ready_garment(client)
    assert g["status"] == "needs_category" and g["note"]
    assert client.post("/tryon", json={"garment_id": g["id"]}).status_code == 409
    r = client.patch(f"/garments/{g['id']}", json={"category": "outerwear"})
    assert r.status_code == 200 and r.json()["status"] == "processing"
    assert client.get(f"/garments/{g['id']}").json()["status"] == "ready"
    assert client.patch(f"/garments/{g['id']}", json={"category": "hat"}).status_code == 422


def test_segmentation_failure_falls_back_to_full_image(client):
    r = add_garment(client, category="top")
    gid = r.json()["id"]
    p = get_provider()
    p.jobs[next(iter(p.jobs))]["fail"] = "seg failed"
    g = client.get(f"/garments/{gid}").json()
    assert g["status"] == "ready" and "full image" in g["note"]


def test_garment_rejects_bad_files(client):
    assert add_garment(client, data=b"nope").json()["code"] == "bad_image"
    assert add_garment(client, data=jpeg(100, 120)).json()["code"] == "low_resolution"
    assert add_garment(client, category="hat").status_code == 422


def test_tryon_flow(client, s3):
    for v in ("front", "side", "back"):
        upload(client, v)
    g = ready_garment(client, category="top")
    r = client.post("/tryon", json={"garment_id": g["id"]})
    assert r.status_code == 202
    t = r.json()
    assert t["status"] == "running" and set(t["views"]) == {"front"} and sorted(t["unsupported_views"]) == ["back", "side"]
    t = client.get(f"/tryon/{t['id']}").json()
    assert t["status"] == "done"
    front = t["views"]["front"]
    assert front["status"] == "succeeded" and "X-Amz-Signature" in front["result_url"] and front["original_url"]
    assert any("/tryons/" in o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test")["Contents"])
    assert [x["id"] for x in client.get("/tryon").json()] == [t["id"]]


def test_tryon_failure_reported(client):
    upload(client, "front")
    g = ready_garment(client, category="top")
    get_provider().fail_next = "CUDA OOM"
    t = client.post("/tryon", json={"garment_id": g["id"]}).json()
    t = client.get(f"/tryon/{t['id']}").json()
    assert t["status"] == "failed" and "CUDA OOM" in t["views"]["front"]["error"]


def test_tryon_requires_front_photo_and_ownership(client):
    g = ready_garment(client, category="top")
    assert "front" in client.post("/tryon", json={"garment_id": g["id"]}).json()["detail"]
    other = make_client("o@example.com")
    upload(other, "front")
    assert other.post("/tryon", json={"garment_id": g["id"]}).status_code == 404
    assert other.get(f"/garments/{g['id']}").status_code == 404


def test_retaken_photo_hides_stale_original(client):
    upload(client, "front")
    g = ready_garment(client, category="top")
    t = client.post("/tryon", json={"garment_id": g["id"]}).json()
    upload(client, "front")
    assert client.get(f"/tryon/{t['id']}").json()["views"]["front"]["original_url"] is None
