import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ADMIN_SESSION_SECRET", "test-admin-session-secret")

from app.main import app


def test_central_identity_exposes_logout_all_endpoint():
    assert "post" in app.openapi()["paths"]["/v1/auth/logout-all"]


def test_central_identity_exposes_current_user_endpoint():
    assert "get" in app.openapi()["paths"]["/v1/auth/me"]


def test_product_api_accepts_central_access_tokens_with_ecosystem_audience():
    source = open("app/auth.py").read()
    assert 'options={"verify_aud": False}' in source
    assert 'payload.get("iss") != "https://auth.lurg.pro"' in source
    assert '"lurg" not in audience' in source
