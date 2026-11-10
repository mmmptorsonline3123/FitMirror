"""ReplicateProvider against a mocked Replicate HTTP API (shapes from docs/reference/http)."""
import base64
import json
import httpx
import pytest
from app.inference import ProviderError, ReplicateProvider
from .conftest import jpeg

# Confirmed 2026-11-10: replicate.com/cuuupid/idm-vton/llms.txt, replicate.com/yorickvp/llava-13b/llms.txt,
# github.com/schananas/grounded_sam_replicate predict.py.
SCHEMAS = {
    "cuuupid/idm-vton": {"garm_img", "garment_des", "human_img", "mask_img", "category", "crop", "force_dc", "mask_only", "steps", "seed"},
    "schananas/grounded_sam": {"image", "mask_prompt", "negative_mask_prompt", "adjustment_factor"},
    "yorickvp/llava-13b": {"image", "prompt", "top_p", "temperature", "max_tokens"},
}


def make(schemas=SCHEMAS, prediction=None):
    sent = []

    def handler(req: httpx.Request):
        path = req.url.path
        if req.url.host == "replicate.delivery":
            assert "authorization" not in req.headers  # token never sent to output CDN
            return httpx.Response(200, content=b"IMG")
        assert req.headers["authorization"] == "Bearer tok"
        if path.startswith("/v1/models/"):
            name = path.removeprefix("/v1/models/")
            props = {k: {} for k in schemas[name]}
            return httpx.Response(200, json={"latest_version": {"id": "v-" + name.split("/")[1],
                                  "openapi_schema": {"components": {"schemas": {"Input": {"properties": props}}}}}})
        if path == "/v1/predictions" and req.method == "POST":
            sent.append(json.loads(req.content))
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        if path == "/v1/predictions/p1":
            return httpx.Response(200, json=prediction)
        return httpx.Response(404)

    p = ReplicateProvider("tok", "cuuupid/idm-vton", "schananas/grounded_sam", "yorickvp/llava-13b", httpx.MockTransport(handler))
    return p, sent


def test_tryon_submit_uses_small_data_urls_and_schema_filtered_optionals():
    p, sent = make()
    big = jpeg(1500, 2500)  # ~ several MB of noise
    assert p.tryon(big, jpeg(), "dress") == "p1"
    body = sent[0]
    assert body["version"] == "v-idm-vton"
    inp = body["input"]
    assert inp["category"] == "dresses" and inp["crop"] is True
    for k in ("human_img", "garm_img"):
        assert inp[k].startswith("data:image/jpeg;base64,")
        assert len(base64.b64decode(inp[k].split(",", 1)[1])) <= 256_000  # Replicate doesn't store data URLs <=256KB


def test_schema_drift_fails_loudly():
    p, _ = make({**SCHEMAS, "cuuupid/idm-vton": {"image", "cloth"}})
    with pytest.raises(ProviderError, match="human_img"):
        p.tryon(jpeg(), jpeg(), "top")


def test_optional_input_dropped_when_absent():
    p, sent = make({**SCHEMAS, "cuuupid/idm-vton": {"human_img", "garm_img", "garment_des"}})
    p.tryon(jpeg(), jpeg(), "top")
    assert "crop" not in sent[0]["input"] and "category" not in sent[0]["input"]


@pytest.mark.parametrize("pred,kind,expect", [
    ({"status": "processing"}, "tryon", ("pending", None)),
    ({"status": "failed", "error": "OOM"}, "tryon", ("failed", None)),
    ({"status": "succeeded", "output": None}, "tryon", ("failed", None)),  # data removed after 1h
    ({"status": "succeeded", "output": "https://replicate.delivery/x.png"}, "tryon", ("succeeded", b"IMG")),
    ({"status": "succeeded", "output": ["a", "b", "https://replicate.delivery/mask.jpg", "d"]}, "segment", ("succeeded", b"IMG")),
    ({"status": "succeeded", "output": ["Out", "erwear"]}, "classify", ("succeeded", "outerwear")),
    ({"status": "succeeded", "output": ["a hat"]}, "classify", ("succeeded", None)),
])
def test_poll(pred, kind, expect):
    p, _ = make(prediction=pred)
    st = p.poll(kind, "p1")
    assert (st.status, st.result) == expect
