import io
import logging
from PIL import Image
from .conftest import jpeg, make_client, upload


def test_upload_valid_photo(client, s3):
    r = upload(client, "front")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["view"] == "front" and "X-Amz-Signature" in body["url"]
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test")["Contents"]]
    assert len(keys) == 1 and keys[0].startswith("users/") and "/photos/front-" in keys[0]
    assert s3.head_object(Bucket="fitmirror-test", Key=keys[0])["ServerSideEncryption"] == "AES256"
    assert [p["view"] for p in client.get("/capture/photos").json()] == ["front"]


def test_rejections_have_specific_reasons(client, s3):
    cases = [
        (upload(client, "front", jpeg(blank=True)), "no_person"),
        (upload(client, "front", jpeg(1000, 600)), "landscape"),
        (upload(client, "front", jpeg(300, 500)), "low_resolution"),
        (upload(client, "front", b"not an image"), "bad_image"),
        (upload(client, "top"), "bad_view"),
    ]
    for r, code in cases:
        assert r.status_code == 422 and r.json()["code"] == code and r.json()["reason"], (code, r.text)
    assert "Contents" not in s3.list_objects_v2(Bucket="fitmirror-test")  # rejected photos are never stored


def test_too_large(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    from app import config
    config.get_settings.cache_clear()
    assert upload(client).json()["code"] == "too_large"


def test_retake_replaces_previous(client, s3):
    upload(client, "side")
    upload(client, "side")
    upload(client, "back")
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="fitmirror-test")["Contents"]]
    assert len(keys) == 2
    assert sorted(p["view"] for p in client.get("/capture/photos").json()) == ["back", "side"]


def test_exif_and_gps_stripped(client, s3):
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0)}  # GPSInfo
    assert upload(client, "front", jpeg(exif=exif.tobytes())).status_code == 201
    key = s3.list_objects_v2(Bucket="fitmirror-test")["Contents"][0]["Key"]
    stored = Image.open(io.BytesIO(s3.get_object(Bucket="fitmirror-test", Key=key)["Body"].read()))
    assert not stored.getexif()


def test_users_cannot_see_each_others_photos(client):
    upload(client, "front")
    assert make_client("other@example.com").get("/capture/photos").json() == []


def test_no_signed_urls_or_keys_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    upload(client, "front")
    upload(client, "front", jpeg(blank=True))
    client.get("/capture/photos")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "X-Amz-Signature" not in text and "users/" not in text
