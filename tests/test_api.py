from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(Settings())) as c:
        yield c


def test_health_and_security_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["provider"] == "template"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "access-control-allow-origin" not in r.headers


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Matchday Intelligence" in r.text


def test_meta_and_events(client):
    meta = client.get("/api/match/7").json()
    assert meta["synthetic"] is True and len(meta["home"]["players"]) == 11
    ev = client.get("/api/match/7/events?limit=10").json()
    assert ev["count"] == 10 and ev["events"][0]["type"] == "kickoff"


@pytest.mark.parametrize(
    "url",
    [
        "/api/match/-1",
        "/api/match/abc",
        "/api/match/7/timeline?mode=hacker",
        "/api/match/7/timeline?lang=de",
        "/api/match/7/timeline?club=<script>",
        "/api/match/7/timeline?club=ZZZ",
        "/api/match/7/timeline?player=ZZZ-99",
        "/api/stream?speed=100000",
        "/api/stream?faults=0.9",
        "/api/match/7/events?limit=0",
    ],
)
def test_input_validation(client, url):
    assert client.get(url).status_code == 422


def test_timeline_and_compare(client):
    meta = client.get("/api/match/7").json()
    club = meta["home"]["id"]
    tl = client.get(f"/api/match/7/timeline?mode=analyst&lang=es&club={club}").json()
    assert tl["count"] > 100 and tl["audience"]["language"] == "es"
    cmp = client.get("/api/match/7/compare?limit=3").json()
    assert cmp["analyst"]["total_overlays"] > cmp["casual"]["total_overlays"]
    assert cmp["analyst"]["top"][0]["audience"]["mode"] == "analyst"


def test_recap_endpoint(client):
    r = client.get("/api/match/7/recap?lang=fr").json()
    assert r["type"] == "recap" and r["audience"]["language"] == "fr" and r["text"]


def test_schema_endpoint(client):
    s = client.get("/api/schema").json()
    assert s["title"] == "Matchday Intelligence OverlayEvent" and "properties" in s


def test_sse_stream_full_match(client):
    kinds, overlays = [], 0
    with client.stream("GET", "/api/stream?seed=5&speed=0&mode=casual&lang=en") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: "):
                kinds.append(line[7:])
            elif line.startswith("data: ") and kinds[-1] == "overlays":
                overlays += len(json.loads(line[6:])["overlays"])
    assert kinds[0] == "meta" and kinds[-1] == "end" and overlays > 30


def test_sse_resume_from_position(client):
    first_t = None
    with client.stream("GET", "/api/stream?seed=5&speed=0&start=2700") as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and '"kind":"overlays"' in line:
                first_t = json.loads(line[6:])["t"]
                break
    assert first_t is not None and first_t >= 2700


def test_cors_only_when_configured():
    app = create_app(Settings(allowed_origins=("https://judge.example",)))
    with TestClient(app) as c:
        ok = c.get("/healthz", headers={"Origin": "https://judge.example"})
        bad = c.get("/healthz", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://judge.example"
    assert "access-control-allow-origin" not in bad.headers
