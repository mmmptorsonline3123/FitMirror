from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # development | test | production
    jwt_secret: str
    jwt_ttl_minutes: int = 60 * 24 * 7
    cookie_secure: bool = True

    mongo_uri: str  # "mongomock://" for tests / local E2E
    mongo_db: str = "fitmirror"

    s3_endpoint_url: str | None = None  # R2: https://<account>.r2.cloudflarestorage.com
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str = "auto"
    signed_url_ttl_seconds: int = 300

    pose_model_path: str = "models/pose_landmarker_full.task"
    pose_detector: str = "mediapipe"  # "fake" only allowed outside production (E2E)
    max_upload_mb: int = 12

    body_model: str = "silhouette"  # "smplx" reserved: needs licensed model files (non-commercial), not implemented

    feature_3d_garment: bool = False  # P5 3D garment preview (labelled "preview" in the UI)
    url_fetch_allow: str = ""  # "host:port,..." exempt from the public-IP/port checks. Test/E2E only; refused in production.

    inference_provider: str = "replicate"  # "fake" only allowed outside production (tests/E2E)
    replicate_api_token: str | None = None
    replicate_tryon_model: str = "cuuupid/idm-vton"  # NON-COMMERCIAL license
    replicate_segment_model: str = "schananas/grounded_sam"
    replicate_classify_model: str = "yorickvp/llava-13b"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.app_env == "production" and s.pose_detector != "mediapipe":
        raise RuntimeError("POSE_DETECTOR must be 'mediapipe' in production")
    if s.app_env == "production" and s.url_fetch_allow:
        raise RuntimeError("URL_FETCH_ALLOW must be empty in production (SSRF protection)")
    if s.app_env == "production" and s.inference_provider == "fake":
        raise RuntimeError("INFERENCE_PROVIDER=fake is not allowed in production")
    return s
