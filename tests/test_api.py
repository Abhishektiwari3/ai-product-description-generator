"""End-to-end API tests in offline mode (no API key, in-memory MongoDB).

Run from the project root:  pytest -q
"""
import io
import os

os.environ["DEFAULT_PROVIDER"] = "mock"
os.environ["OPENAI_API_KEY"] = ""  # tests never call paid APIs, even if a .env has keys
os.environ["GEMINI_API_KEY"] = ""
os.environ["MONGO_URI"] = "mongodb://localhost:1"  # force the in-memory fallback
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from backend.main import EXAMPLE, app  # noqa: E402

client = TestClient(app)


def photo_bytes():
    img = Image.new("RGB", (600, 600), (245, 245, 245))
    ImageDraw.Draw(img).rounded_rectangle([220, 120, 380, 520], 40, fill=(200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_health():
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["default_provider"] == "mock"


def test_text_to_text_slide12_example():
    r = client.post("/api/generate/text-to-text", json=EXAMPLE)
    assert r.status_code == 200
    d = r.json()["draft"]
    assert "stainless steel" in d["title"].lower()
    assert any("Size" in f for f in d["review_flags"])  # capacity not supplied -> flagged, not invented
    assert not any(ch.isdigit() for ch in d["description"])


def test_validation_errors_are_field_level():
    r = client.post("/api/generate/text-to-text", json={**EXAMPLE, "product_name": "", "brand_voice": "angry"})
    assert r.status_code == 422
    fields = r.json()["error"]["fields"]
    assert "product_name" in fields and "brand_voice" in fields


def test_text_to_image():
    r = client.post("/api/generate/text-to-image", json={"facts": EXAMPLE, "style": "vibrant_pop"})
    assert r.status_code == 200
    img = client.get(r.json()["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_image_to_text_and_image_to_image():
    files = {"image": ("p.jpg", photo_bytes(), "image/jpeg")}
    r = client.post("/api/generate/image-to-text", files=files, data={"category": "Drinkware"})
    assert r.status_code == 200
    assert any("NOT verified" in f for f in r.json()["draft"]["review_flags"])
    r = client.post("/api/generate/image-to-image", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
                    data={"style": "luxury_dark"})
    assert r.status_code == 200 and r.json()["image_url"].startswith("/api/images/")


def test_bad_upload_rejected():
    r = client.post("/api/generate/image-to-image", files={"image": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_image"


def test_review_export_history_and_metrics():
    gid = client.post("/api/generate/text-to-text", json=EXAMPLE).json()["id"]
    draft = client.get(f"/api/history/{gid}").json()["output"]
    r = client.post(f"/api/history/{gid}/review", json={"approved": True, "rating": 4, "total_claims": 3,
                                                       "unsupported_claims": 0, "seconds_to_approve": 21.5})
    assert r.json()["saved"]
    for fmt in ["plain", "html", "json", "csv"]:
        e = client.post("/api/export", json={"draft": draft, "format": fmt, "product_name": "Bottle"})
        assert e.status_code == 200 and draft["title"] in e.text
    m = client.get("/api/metrics").json()
    assert m["approved"] >= 1 and m["avg_rating"] is not None
    assert client.delete(f"/api/history/{gid}").json()["deleted"]


def test_brand_voice_roundtrip():
    vid = client.post("/api/voices", json={"name": "Test voice", "base_tone": "minimal", "rules": "Short."}).json()["id"]
    assert any(v["id"] == vid for v in client.get("/api/voices").json()["items"])
    r = client.post("/api/generate/text-to-text", json={**EXAMPLE, "brand_voice_id": vid})
    assert "Test voice" in r.json()["prompt"]["user"]
    assert client.delete(f"/api/voices/{vid}").json()["deleted"]
