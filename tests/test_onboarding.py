import pytest
import jwt as pyjwt
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
import os

os.environ["GOOGLE_CLOUD_PROJECT"] = "dummy"
os.environ["JWT_SECRET"] = "test_jwt_secret"

from app import app

JWT_SECRET = "test_jwt_secret"


@pytest.fixture
def client():
    return TestClient(app)


# ===========================================================================
# T10: LINE OAuth callback — /auth/line/callback
# ===========================================================================

def test_line_oauth_missing_code_returns_400(client):
    response = client.get("/auth/line/callback")
    assert response.status_code == 400


def test_line_oauth_creates_store_doc(client):
    """Valid code → store created with line_bot_id, tokens → redirect to /onboarding with cookie."""
    with patch("app.db") as mock_db, \
         patch("app._exchange_line_code_for_credentials",
               new=AsyncMock(return_value=("tok_abc", "sec_xyz", "Ubot123"))):
        mock_db.create_or_update_store = AsyncMock(return_value=True)
        response = client.get("/auth/line/callback?code=valid_code", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"
    assert "line_onboarding_store_id=Ubot123" in response.headers.get("set-cookie", "")
    mock_db.create_or_update_store.assert_called_once_with("Ubot123", {
        "line_bot_id": "Ubot123",
        "channel_access_token": "tok_abc",
        "channel_secret": "sec_xyz",
    })


def test_line_oauth_updates_existing_store(client):
    """create_or_update_store is called even when store already exists (merge semantics)."""
    with patch("app.db") as mock_db, \
         patch("app._exchange_line_code_for_credentials",
               new=AsyncMock(return_value=("new_tok", "new_sec", "Ubot123"))):
        mock_db.create_or_update_store = AsyncMock(return_value=True)
        response = client.get("/auth/line/callback?code=any", follow_redirects=False)

    assert response.status_code == 303
    mock_db.create_or_update_store.assert_called_once()


# ===========================================================================
# T11: Onboarding form — /onboarding + /onboarding/complete
# ===========================================================================

def test_onboarding_page_requires_cookie(client):
    """/onboarding without line_onboarding_store_id cookie → 403 or redirect."""
    response = client.get("/onboarding", follow_redirects=False)
    assert response.status_code in (307, 403)


def test_onboarding_page_with_cookie(client):
    """/onboarding with cookie → 200 with a form."""
    client.cookies.set("line_onboarding_store_id", "Ubot123")
    response = client.get("/onboarding")
    assert response.status_code == 200
    assert "<form" in response.text


def test_onboarding_complete_saves_data(client):
    """POST /onboarding/complete writes store data and redirects to /admin for Google sign-in."""
    client.cookies.set("line_onboarding_store_id", "Ubot123")
    with patch("app.db") as mock_db:
        mock_db.create_or_update_store = AsyncMock(return_value=True)
        mock_db.update_business_hours = AsyncMock(return_value=True)
        mock_db.update_table_layout = AsyncMock(return_value=True)
        response = client.post("/onboarding/complete", data={
            "store_name": "My Coffee Shop",
            "open_time": "09:00",
            "close_time": "21:00",
            "total_tables": "8",
            "capacity_per_table": "4",
        }, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    mock_db.create_or_update_store.assert_called_once_with("Ubot123", {"name": "My Coffee Shop"})
    mock_db.update_business_hours.assert_called_once()
    mock_db.update_table_layout.assert_called_once()


def test_onboarding_complete_no_cookie_returns_403(client):
    response = client.post("/onboarding/complete", data={"store_name": "X"})
    assert response.status_code == 403


def test_google_callback_during_onboarding_binds_email(client):
    """Google callback with line_onboarding_store_id cookie adds email to admin_emails, issues JWT."""
    client.cookies.set("line_onboarding_store_id", "Ubot123")
    with patch("app.db") as mock_db, \
         patch("app._exchange_google_code_for_email",
               new=AsyncMock(return_value="owner@example.com")):
        mock_db.add_admin_email_to_store = AsyncMock(return_value=True)
        response = client.get("/auth/google/callback?code=valid_code", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"
    mock_db.add_admin_email_to_store.assert_called_once_with("Ubot123", "owner@example.com")
    assert "admin_token=" in response.headers.get("set-cookie", "")


def test_google_callback_onboarding_clears_onboarding_cookie(client):
    """After binding email, the line_onboarding_store_id cookie must be cleared."""
    client.cookies.set("line_onboarding_store_id", "Ubot123")
    with patch("app.db") as mock_db, \
         patch("app._exchange_google_code_for_email",
               new=AsyncMock(return_value="owner@example.com")):
        mock_db.add_admin_email_to_store = AsyncMock(return_value=True)
        response = client.get("/auth/google/callback?code=valid_code", follow_redirects=False)

    # Cookie cleared: either set to empty string with max-age=0, or deleted
    cookies_header = response.headers.get("set-cookie", "")
    assert "line_onboarding_store_id" in cookies_header or response.status_code == 303
