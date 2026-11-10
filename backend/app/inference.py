"""GPU inference behind one interface. Render has no GPU: jobs run at a provider, we submit + poll.

Privacy: images are sent as data URLs <=256 KB, which Replicate documents it does NOT store. Replicate also
auto-deletes API prediction inputs/outputs/logs after 1 hour. We download outputs into our bucket on first poll.
"""
import base64
import io
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol
import httpx
from PIL import Image
from .config import get_settings

CATEGORIES = ("dress", "top", "bottom", "outerwear")
Kind = Literal["classify", "segment", "tryon"]


@dataclass
class JobState:
    status: Literal["pending", "succeeded", "failed"]
    result: bytes | str | None = None  # classify -> category str; segment -> mask PNG/JPEG bytes; tryon -> image bytes
    error: str | None = None


class ProviderError(Exception):
    pass


class InferenceProvider(Protocol):
    name: str
    tryon_views: tuple[str, ...]  # which capture views the try-on model handles

    def classify_garment(self, garment_jpeg: bytes) -> str: ...
    def segment_garment(self, garment_jpeg: bytes, category: str) -> str: ...
    def tryon(self, person_jpeg: bytes, garment_jpeg: bytes, category: str) -> str: ...
    def poll(self, kind: Kind, job_id: str) -> JobState: ...


def fit_jpeg(data: bytes, max_side: int = 1024, limit: int = 250_000) -> bytes:
    """Re-encode so it fits in a data URL Replicate won't store (<=256 KB)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    for q in (88, 80, 70, 60, 50, 40):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        if buf.tell() <= limit:
            return buf.getvalue()
        if q == 60:
            img.thumbnail((img.width * 3 // 4, img.height * 3 // 4))
    raise ProviderError("could not shrink image under data-URL limit")


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(fit_jpeg(jpeg)).decode()


SEGMENT_PROMPTS = {"dress": "dress", "top": "shirt, top, blouse", "bottom": "pants, skirt, shorts", "outerwear": "jacket, coat"}
VTON_CATEGORY = {"dress": "dresses", "top": "upper_body", "outerwear": "upper_body", "bottom": "lower_body"}


class ReplicateProvider:
    """Model refs are env-configurable. Input names are checked against each model's live OpenAPI schema on first use,
    so a model whose schema drifts fails loudly with the missing field instead of producing garbage."""
    name = "replicate"
    tryon_views = ("front",)  # IDM-VTON is trained on front-facing images; side/back are reported as unsupported
    API = "https://api.replicate.com/v1"

    def __init__(self, token: str, tryon_model: str, segment_model: str, classify_model: str, transport=None):
        self.http = httpx.Client(base_url=self.API, headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport)
        self.dl = httpx.Client(timeout=60, follow_redirects=True, transport=transport)  # no auth header on output downloads
        self.models = {"tryon": tryon_model, "segment": segment_model, "classify": classify_model}
        self._versions: dict[str, tuple[str, set[str]]] = {}

    def _version(self, kind: Kind) -> tuple[str, set[str]]:
        if kind not in self._versions:
            r = self.http.get(f"/models/{self.models[kind]}")
            r.raise_for_status()
            v = r.json()["latest_version"]
            props = set(v["openapi_schema"]["components"]["schemas"]["Input"]["properties"])
            self._versions[kind] = (v["id"], props)
        return self._versions[kind]

    def _submit(self, kind: Kind, required: dict, optional: dict | None = None) -> str:
        version, props = self._version(kind)
        missing = set(required) - props
        if missing:
            raise ProviderError(f"{self.models[kind]} schema has no input(s) {sorted(missing)}; update ReplicateProvider")
        inp = {**required, **{k: v for k, v in (optional or {}).items() if k in props}}
        r = self.http.post("/predictions", json={"version": version, "input": inp})
        r.raise_for_status()
        return r.json()["id"]

    def classify_garment(self, garment_jpeg):
        q = "What kind of clothing item is the main product in this image? Answer with exactly one word: dress, top, bottom, or outerwear."
        return self._submit("classify", {"image": data_url(garment_jpeg), "prompt": q}, {"temperature": 0.1, "max_tokens": 8})

    def segment_garment(self, garment_jpeg, category):
        return self._submit("segment", {"image": data_url(garment_jpeg), "mask_prompt": SEGMENT_PROMPTS[category],
                                        "negative_mask_prompt": "face, hair, skin, hands, background"})

    def tryon(self, person_jpeg, garment_jpeg, category):
        return self._submit("tryon", {"human_img": data_url(person_jpeg), "garm_img": data_url(garment_jpeg),
                                      "garment_des": f"a {category}"},
                            {"category": VTON_CATEGORY[category], "crop": True})

    def poll(self, kind, job_id):
        r = self.http.get(f"/predictions/{job_id}")
        r.raise_for_status()
        p = r.json()
        if p["status"] in ("starting", "processing"):
            return JobState("pending")
        if p["status"] != "succeeded":
            return JobState("failed", error=(p.get("error") or p["status"])[:300])
        out = p.get("output")
        if out is None:
            return JobState("failed", error="Result expired at the provider before it was saved. Please run it again.")
        if kind == "classify":
            text = ("".join(out) if isinstance(out, list) else str(out)).lower()
            return JobState("succeeded", next((c for c in CATEGORIES if c in text), None))
        url = out[2] if kind == "segment" else (out[0] if isinstance(out, list) else out)  # grounded_sam yields [annotated, neg_annotated, mask, inverted_mask]
        img = self.dl.get(url)
        img.raise_for_status()
        return JobState("succeeded", img.content)


class FakeProvider:
    """Test/E2E only (refused in production). Every job is pending on its first poll, then succeeds."""
    name = "fake"
    tryon_views = ("front",)

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.fail_next: str | None = None  # tests set this to make the next submitted job fail

    def _new(self, **job) -> str:
        jid = uuid.uuid4().hex
        self.jobs[jid] = {**job, "polls": 0, "fail": self.fail_next}
        self.fail_next = None
        return jid

    def classify_garment(self, garment_jpeg):
        fit_jpeg(garment_jpeg)  # exercise the data-URL path
        return self._new(result="top")

    def segment_garment(self, garment_jpeg, category):
        img = Image.open(io.BytesIO(garment_jpeg))
        mask = Image.new("L", img.size, 0)
        mask.paste(255, (img.width // 4, img.height // 4, img.width * 3 // 4, img.height * 3 // 4))
        buf = io.BytesIO()
        mask.save(buf, "PNG")
        return self._new(result=buf.getvalue())

    def tryon(self, person_jpeg, garment_jpeg, category):
        img = Image.open(io.BytesIO(fit_jpeg(person_jpeg))).convert("RGB")
        img.paste((180, 40, 90), (img.width // 4, img.height // 4, img.width * 3 // 4, img.height // 2))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return self._new(result=buf.getvalue())

    def poll(self, kind, job_id):
        job = self.jobs[job_id]
        job["polls"] += 1
        if job["polls"] == 1:
            return JobState("pending")
        if job["fail"]:
            return JobState("failed", error=job["fail"])
        return JobState("succeeded", job["result"])


@lru_cache
def get_provider() -> InferenceProvider:
    s = get_settings()
    if s.inference_provider == "fake":
        return FakeProvider()
    if not s.replicate_api_token:
        raise ProviderError("REPLICATE_API_TOKEN is not set")
    return ReplicateProvider(s.replicate_api_token, s.replicate_tryon_model, s.replicate_segment_model, s.replicate_classify_model)
