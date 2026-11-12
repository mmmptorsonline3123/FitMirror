"""Local E2E backend: in-process moto S3 + mongomock + fake pose detector. Never use in production."""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import boto3
import uvicorn
from moto.server import ThreadedMotoServer

S3_PORT = 5055
SHOP_PORT = 5056  # fixture product pages for the URL-ingestion flow
os.environ.update({
    "APP_ENV": "test", "JWT_SECRET": "e2e-secret-0123456789abcdef0123456789", "COOKIE_SECURE": "false",
    "MONGO_URI": "mongomock://", "S3_ENDPOINT_URL": f"http://127.0.0.1:{S3_PORT}", "S3_BUCKET": "fitmirror-e2e",
    "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_REGION": "us-east-1", "POSE_DETECTOR": "fake", "INFERENCE_PROVIDER": "fake", "URL_FETCH_ALLOW": f"127.0.0.1:{SHOP_PORT}", "FEATURE_3D_GARMENT": "true",
})

def _relative_signed_urls():
    """Demo/E2E: hand out '/fitmirror-e2e/users/...?sig' instead of 'http://127.0.0.1:5055/...', which a phone on
    the LAN can't reach. Vite proxies that path to moto. Real R2 URLs (production) are already public HTTPS hosts."""
    from urllib.parse import urlsplit
    from app import storage
    absolute = storage.Storage.signed_url

    def relative(self, key):
        u = urlsplit(absolute(self, key))
        return f"{u.path}?{u.query}"
    storage.Storage.signed_url = relative


if __name__ == "__main__":
    _relative_signed_urls()
    ThreadedMotoServer(ip_address="127.0.0.1", port=S3_PORT).start()
    boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"], region_name="us-east-1",
                 aws_access_key_id="test", aws_secret_access_key="test").create_bucket(Bucket="fitmirror-e2e")
    shop = Path(__file__).resolve().parent.parent / "frontend" / "e2e" / "fixtures" / "shop"
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(shop))
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", SHOP_PORT), handler).serve_forever, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
