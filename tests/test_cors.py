"""
CORS tests for naver-place-collector (S3b-1.5).
Verifies CORSMiddleware allows itdalab.com and does not reflect other origins.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")
os.environ.setdefault("COLLECTOR_API_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


# T1 — GET /health with itdalab.com origin → ACAO header reflected
def test_cors_allows_itdalab_origin():
    resp = client.get("/health", headers={"Origin": "https://itdalab.com"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://itdalab.com"


# T2 — OPTIONS preflight on place trigger → 200/204 + ACAO present
def test_cors_preflight():
    resp = client.options(
        "/api/v1/places/1234567890/collect-visitor-reviews",
        headers={
            "Origin": "https://itdalab.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "https://itdalab.com"


# T3 — evil origin → ACAO header NOT reflected
def test_cors_other_origin_not_reflected():
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    acao = resp.headers.get("access-control-allow-origin", "")
    assert "evil.example" not in acao
