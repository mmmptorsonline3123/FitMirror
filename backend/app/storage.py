"""Private S3-compatible object storage (Cloudflare R2 in prod). Objects are never public; reads go through short-lived signed URLs.
R2 encrypts all objects at rest (AES-256) by default; ServerSideEncryption is also requested for AWS-S3 compatibility."""
from functools import lru_cache
import boto3
from botocore.config import Config
from .config import get_settings


class Storage:
    def __init__(self):
        s = get_settings()
        self.bucket = s.s3_bucket
        self.ttl = s.signed_url_ttl_seconds
        self.sse = s.s3_endpoint_url is None or "amazonaws" in (s.s3_endpoint_url or "")  # R2 rejects the SSE header; it encrypts anyway
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        extra = {"ServerSideEncryption": "AES256"} if self.sse else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                n += len(keys)
        return n

    def list_prefix(self, prefix: str) -> list[str]:
        resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return [o["Key"] for o in resp.get("Contents", [])]


@lru_cache
def get_storage() -> Storage:
    return Storage()


def user_prefix(user_id: str) -> str:
    return f"users/{user_id}/"
