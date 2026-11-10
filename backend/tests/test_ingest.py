import io
import json
import logging
import socket
import httpx
import pytest
from PIL import Image
from app import tryon
from app.config import get_settings
from app.fetch import BlockedAddress, Fetcher, FetchError, GuardedBackend, check_url, is_public, make_client
from app.product_page import parse_product_page
from .test_tryon import garment_jpeg

# ---------- page parsing ----------

SIZE_TABLE = """<table><tr><th>Size</th><th>Bust (cm)</th><th>Waist (cm)</th><th>Hip (cm)</th></tr>
<tr><td>S</td><td>84-88</td><td>66 – 70</td><td>90-94</td></tr>
<tr><td>M</td><td>89-93</td><td>71-75</td><td>95-99</td></tr>
<tr><td>L</td><td>94-98</td><td>76-80</td><td>100-104</td></tr></table>"""

PRODUCT = f"""<html><head><title>Shop | Linen Dress</title>
<meta property="og:image" content="/img/og.jpg"><meta property="og:title" content="OG title">
<script type="application/ld+json">{json.dumps({"@context": "https://schema.org", "@graph": [
    {"@type": "BreadcrumbList"}, {"@type": "Product", "name": "Linen Midi Dress",
                                  "image": ["https://cdn.shop.test/a.jpg", {"@type": "ImageObject", "url": "/b.jpg"}]}]})}</script>
</head><body><table><tr><td>Shipping</td><td>Free</td></tr></table>{SIZE_TABLE}</body></html>"""


def test_parse_product_jsonld_og_and_size_chart():
    p = parse_product_page(PRODUCT, "https://shop.test/p/dress?ref=x")
    assert p.title == "Linen Midi Dress"
    assert p.images == ["https://cdn.shop.test/a.jpg", "https://shop.test/b.jpg", "https://shop.test/img/og.jpg"]
    chart = p.size_chart
    assert [s.label for s in chart.sizes] == ["S", "M", "L"]
    assert chart.sizes[0].ranges == {"chest": (84, 88), "waist": (66, 70), "hip": (90, 94)}


def test_parse_og_only_and_title_fallback():
    p = parse_product_page('<title> Cool Tee </title><meta name="twitter:image" content="https://x.test/t.png">', "https://x.test/")
    assert p.title == "Cool Tee" and p.images == ["https://x.test/t.png"] and p.size_chart is None


def test_transposed_inches_chart():
    html = """<table><tr><th>Measurement (inches)</th><th>XS</th><th>S</th><th>M</th></tr>
    <tr><td>Chest</td><td>32-34</td><td>35-37</td><td>38-40</td></tr>
    <tr><td>Waist</td><td>26-28</td><td>29-31</td><td>32-34</td></tr></table>"""
    c = parse_product_page(html, "https://a.test/").size_chart
    assert [s.label for s in c.sizes] == ["XS", "S", "M"]
    assert c.sizes[0].ranges["chest"] == pytest.approx((32 * 2.54, 34 * 2.54))


def test_prefers_cm_column_and_guesses_unitless_inches():
    both = """<table><tr><th>Size</th><th>Waist (in)</th><th>Waist (cm)</th></tr>
    <tr><td>30</td><td>30</td><td>76</td></tr><tr><td>32</td><td>32</td><td>81</td></tr></table>"""
    c = parse_product_page(both, "https://a.test/").size_chart
    assert c.sizes[0].ranges["waist"] == (74, 78)  # point 76 cm -> +-2
    unitless = """<table><tr><th>Size</th><th>Chest</th><th>Shoulder</th></tr>
    <tr><td>M</td><td>38-40</td><td>18</td></tr><tr><td>L</td><td>41-43</td><td>19</td></tr></table>"""
    c = parse_product_page(unitless, "https://a.test/").size_chart
    assert c.sizes[0].ranges["chest"] == pytest.approx((38 * 2.54, 40 * 2.54))


def test_non_chart_tables_ignored():
    html = "<table><tr><th>Size</th><th>Price</th></tr><tr><td>S</td><td>20</td></tr><tr><td>M</td><td>22</td></tr></table>"
    assert parse_product_page(html, "https://a.test/").size_chart is None


# ---------- SSRF guard ----------

@pytest.mark.parametrize("ip,ok", [("93.184.216.34", True), ("127.0.0.1", False), ("10.1.2.3", False), ("192.168.0.1", False),
                                   ("169.254.169.254", False), ("0.0.0.0", False), ("::1", False), ("fd00::1", False),
                                   ("::ffff:127.0.0.1", False), ("100.64.0.1", False), ("224.0.0.1", False)])
def test_is_public(ip, ok):
    assert is_public(ip) is ok


@pytest.mark.parametrize("url", ["ftp://a.test/x", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@a.test/",
                                 "http://a.test:8080/", "http://a.test:22/", "not a url", "https://[::1"])
def test_check_url_rejects(url):
    with pytest.raises(FetchError) as e:
        check_url(url)
    assert e.value.code == "bad_url"


def test_guarded_backend_checks_resolved_addresses():
    def resolver(*ips):
        return lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    with pytest.raises(BlockedAddress):
        GuardedBackend(set(), resolver("10.0.0.7")).connect_tcp("rebind.test", 443)
    with pytest.raises(BlockedAddress):  # any private answer poisons the set
        GuardedBackend(set(), resolver("93.184.216.34", "127.0.0.1")).connect_tcp("mixed.test", 443)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/", "http://10.0.0.5/", "http://0.0.0.0/"])
def test_real_client_refuses_private_targets(url):
    """No mocks: the guarded connection pool refuses before any byte is sent."""
    with make_client() as c, pytest.raises(FetchError) as e:
        Fetcher(c).get(url, 1000, ())
    assert e.value.code == "unsafe_url"


# ---------- fetcher behaviour (mock HTTP) ----------

def jpeg_bytes():
    return garment_jpeg()


def mock_site(routes: dict):
    """routes: url -> (status, headers, body) or callable. Records requested URLs."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        assert req.headers["user-agent"].startswith("FitMirrorBot")
        r = routes.get(str(req.url), (404, {}, b""))
        status, headers, body = r
        return httpx.Response(status, headers=headers, content=body)
    return make_client(transport=httpx.MockTransport(handler)), seen


HTML = {"content-type": "text/html; charset=utf-8"}
JPG = {"content-type": "image/jpeg"}


def test_robots_disallow_and_rfc9309_status_rules():
    c, _ = mock_site({"https://a.test/robots.txt": (200, {}, b"User-agent: *\nDisallow: /p/\n"),
                      "https://a.test/p/1": (200, HTML, b"<html></html>")})
    with pytest.raises(FetchError) as e:
        Fetcher(c).get("https://a.test/p/1", 1000, ("text/html",))
    assert e.value.code == "blocked_by_robots"
    c, _ = mock_site({"https://b.test/p/1": (200, HTML, b"ok")})  # robots 404 -> allowed
    assert Fetcher(c).get("https://b.test/p/1", 1000, ("text/html",))[1] == b"ok"
    c, _ = mock_site({"https://c.test/robots.txt": (503, {}, b""), "https://c.test/p/1": (200, HTML, b"ok")})  # 5xx -> disallow
    with pytest.raises(FetchError):
        Fetcher(c).get("https://c.test/p/1", 1000, ("text/html",))


def test_redirects_rechecked_and_limits():
    c, seen = mock_site({"https://a.test/x": (302, {"location": "ftp://a.test/y"}, b"")})
    with pytest.raises(FetchError) as e:
        Fetcher(c).get("https://a.test/x", 1000, ())
    assert e.value.code == "bad_url"
    c, _ = mock_site({"https://a.test/big": (200, HTML, b"x" * 5000)})
    with pytest.raises(FetchError) as e:
        Fetcher(c).get("https://a.test/big", 1000, ("text/html",))
    assert e.value.code == "too_large"
    c, _ = mock_site({"https://a.test/pdf": (200, {"content-type": "application/pdf"}, b"%PDF")})
    with pytest.raises(FetchError) as e:
        Fetcher(c).get("https://a.test/pdf", 1000, ("text/html",))
    assert e.value.code == "wrong_type"
    loop = {f"https://a.test/r{i}": (301, {"location": f"/r{i + 1}"}, b"") for i in range(10)}
    c, _ = mock_site(loop)
    with pytest.raises(FetchError, match="redirects"):
        Fetcher(c).get("https://a.test/r0", 1000, ())


# ---------- endpoint ----------

@pytest.fixture
def site(monkeypatch):
    routes = {}
    seen = []

    def factory(allow=frozenset(), **_):
        c, s = mock_site(routes)
        seen.append(s)
        return c
    monkeypatch.setattr(tryon, "make_client", factory)
    return routes


def test_from_url_success_with_size_chart_and_image_fallback(client, site, caplog):
    site["https://shop.test/p/dress?session=SECRET"] = (200, HTML, PRODUCT.encode())
    site["https://shop.test/b.jpg"] = (200, JPG, jpeg_bytes())  # a.jpg 404s -> falls back to b.jpg
    caplog.set_level(logging.INFO)
    r = client.post("/garments/from-url", json={"url": "https://shop.test/p/dress?session=SECRET"})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["source"] == "url" and g["title"] == "Linen Midi Dress" and g["source_url"].startswith("https://shop.test/p/dress")
    assert [s["label"] for s in g["size_chart"]["sizes"]] == ["S", "M", "L"]
    for _ in range(5):
        g = client.get(f"/garments/{g['id']}").json()
    assert g["status"] == "ready" and g["category"] == "top"  # FakeProvider classifier
    assert "SECRET" not in caplog.text


def test_from_url_failures_ask_for_screenshot(client, site):
    site["https://shop.test/robots.txt"] = (200, {}, b"User-agent: FitMirrorBot\nDisallow: /\n")
    r = client.post("/garments/from-url", json={"url": "https://shop.test/p/1"})
    assert r.status_code == 422 and r.json()["code"] == "blocked_by_robots" and r.json()["fallback"] == "screenshot"
    assert "screenshot" in r.json()["reason"]
    site["https://other.test/p"] = (200, HTML, b"<html><title>No pics</title></html>")
    assert client.post("/garments/from-url", json={"url": "https://other.test/p"}).json()["code"] == "no_image"
    site["https://img.test/p"] = (200, HTML, b'<meta property="og:image" content="https://img.test/tiny.jpg">')
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, "JPEG")
    site["https://img.test/tiny.jpg"] = (200, JPG, buf.getvalue())
    assert client.post("/garments/from-url", json={"url": "https://img.test/p"}).json()["code"] == "no_image"
    assert client.get("/garments").json() == []


def test_from_url_ssrf_end_to_end(client):
    r = client.post("/garments/from-url", json={"url": "http://169.254.169.254/latest/meta-data/iam"})
    assert r.status_code == 422 and r.json()["code"] == "unsafe_url"
    assert client.post("/garments/from-url", json={"url": "gopher://a.test/x"}).json()["code"] == "bad_url"


def test_from_url_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).post("/garments/from-url", json={"url": "https://shop.test/p"}).status_code == 401


def test_allowlist_refused_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSE_DETECTOR", "mediapipe")
    monkeypatch.setenv("INFERENCE_PROVIDER", "replicate")
    monkeypatch.setenv("URL_FETCH_ALLOW", "127.0.0.1:5056")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="URL_FETCH_ALLOW"):
        get_settings()
