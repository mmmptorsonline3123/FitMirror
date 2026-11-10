import os

os.environ.update({
    "APP_ENV": "test", "JWT_SECRET": "test-secret-not-for-prod-0123456789abcdef", "COOKIE_SECURE": "false",
    "MONGO_URI": "mongomock://", "S3_BUCKET": "fitmirror-test", "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test",
    "S3_REGION": "us-east-1", "POSE_DETECTOR": "fake", "INFERENCE_PROVIDER": "fake",
})

import io  # noqa: E402
import boto3  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402
from PIL import Image  # noqa: E402
from app import body, config, db, inference, pose, storage  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def env():
    for f in (config.get_settings, db.get_db, storage.get_storage, pose.get_detector, inference.get_provider, body.get_body_model):
        f.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="fitmirror-test")
        yield


@pytest.fixture
def s3():
    return boto3.client("s3", region_name="us-east-1")


def make_client(email="a@example.com"):
    c = TestClient(app)
    r = c.post("/auth/register", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 201, r.text
    return c


@pytest.fixture
def client():
    return make_client()


def jpeg(w=600, h=1000, blank=False, exif=None) -> bytes:
    arr = np.full((h, w, 3), 200, np.uint8) if blank else np.random.default_rng(0).integers(0, 255, (h, w, 3), np.uint8)
    buf = io.BytesIO()
    kw = {"exif": exif} if exif else {}
    Image.fromarray(arr).save(buf, "JPEG", **kw)
    return buf.getvalue()


def upload(c, view="front", data=None):
    data = data or (jpeg(500, 1000) if view == "side" else jpeg())  # FakeDetector: narrow image = side pose
    return c.post("/capture/photos", data={"view": view}, files={"file": ("p.jpg", data, "image/jpeg")})
