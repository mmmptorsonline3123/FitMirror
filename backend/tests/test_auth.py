from fastapi.testclient import TestClient
from app.main import app
from .conftest import make_client


def test_register_sets_httponly_cookie():
    c = TestClient(app)
    r = c.post("/auth/register", json={"email": "New@Example.com", "password": "correct-horse-battery"})
    assert r.status_code == 201
    assert r.json()["email"] == "new@example.com"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "password_hash" not in r.json()
    assert c.get("/me").status_code == 200


def test_register_duplicate_and_weak_password():
    make_client("dup@example.com")
    c = TestClient(app)
    assert c.post("/auth/register", json={"email": "dup@example.com", "password": "correct-horse-battery"}).status_code == 409
    assert c.post("/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422


def test_login_logout():
    make_client("l@example.com")
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": "l@example.com", "password": "wrong-password-123"}).status_code == 401
    assert c.post("/auth/login", json={"email": "l@example.com", "password": "correct-horse-battery"}).status_code == 200
    assert c.get("/me").status_code == 200
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/me").status_code == 401


def test_unauthenticated_and_tampered_token():
    c = TestClient(app)
    assert c.get("/me").status_code == 401
    assert c.get("/capture/photos").status_code == 401
    c.cookies.set("fm_session", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.bogus")
    assert c.get("/me").status_code == 401


def test_profile_height_weight(client):
    r = client.patch("/me", json={"height_cm": 172.5})
    assert r.status_code == 200 and r.json()["height_cm"] == 172.5 and r.json()["weight_kg"] is None
    assert client.patch("/me", json={"height_cm": 20}).status_code == 422
    assert client.patch("/me", json={"weight_kg": 70}).json()["height_cm"] == 172.5
