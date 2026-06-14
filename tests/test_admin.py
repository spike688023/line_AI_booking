import pytest
import jwt as pyjwt
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch, create_autospec
import os
import hmac
import hashlib
import base64
import json

# Set dummy env vars before importing app to avoid errors
os.environ["GOOGLE_CLOUD_PROJECT"] = "dummy"
os.environ["JWT_SECRET"] = "test_jwt_secret"

from app import app
from src.database import Database

JWT_SECRET = "test_jwt_secret"

def _make_admin_jwt(store_id: str = "store_abc", email: str = "admin@example.com") -> str:
    return pyjwt.encode({"store_id": store_id, "email": email}, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_db():
    mock = create_autospec(Database, instance=True)
    mock.get_all_reservations = AsyncMock(return_value=[
        {"id": "1", "date": "2025-12-25", "time": "12:00", "name": "Test User", "phone": "0912345678", "pax": 2}
    ])
    mock.delete_past_reservations = AsyncMock(return_value=5)
    mock.delete_reservation = AsyncMock(return_value=True)
    mock.modify_reservation = AsyncMock(return_value="success")
    with patch("app.db", mock):
        yield mock


# --- T8: Google SSO login page ---

def test_admin_login_page_shows_google_signin(client):
    """GET /admin must render a Google sign-in link, not a password form."""
    response = client.get("/admin")
    assert response.status_code == 200
    assert "google" in response.text.lower() or "Sign in" in response.text


def test_admin_login_page_no_password_form(client):
    """Password form must be gone."""
    response = client.get("/admin")
    assert 'name="password"' not in response.text
    assert "/admin/login" not in response.text


# --- T8: Google OAuth callback ---

def test_google_callback_valid_code_sets_jwt_cookie(client):
    """Valid OAuth code → JWT cookie set → redirect to /admin/dashboard."""
    fake_id_token = pyjwt.encode({"email": "admin@example.com", "sub": "123"}, "any", algorithm="HS256")

    with patch("app.db") as mock_db, \
         patch("app._exchange_google_code_for_email", new=AsyncMock(return_value="admin@example.com")):
        mock_db.get_store_by_admin_email = AsyncMock(return_value={"store_id": "store_abc"})
        response = client.get("/auth/google/callback?code=valid_code", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"
    set_cookie = response.headers.get("set-cookie", "")
    assert "admin_token=" in set_cookie
    assert "HttpOnly" in set_cookie


def test_google_callback_jwt_contains_correct_claims(client):
    """Issued JWT must contain store_id and email claims."""
    with patch("app.db") as mock_db, \
         patch("app._exchange_google_code_for_email", new=AsyncMock(return_value="admin@example.com")):
        mock_db.get_store_by_admin_email = AsyncMock(return_value={"store_id": "store_abc"})
        response = client.get("/auth/google/callback?code=valid_code", follow_redirects=False)

    set_cookie = response.headers.get("set-cookie", "")
    token = set_cookie.split("admin_token=")[1].split(";")[0]
    claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert claims["store_id"] == "store_abc"
    assert claims["email"] == "admin@example.com"


def test_google_callback_unknown_email_creates_store(client):
    """Email not in any store → auto-create store → redirect to /admin/line-settings."""
    with patch("app.db") as mock_db, \
         patch("app._exchange_google_code_for_email", new=AsyncMock(return_value="nobody@example.com")):
        mock_db.get_store_by_admin_email = AsyncMock(return_value=None)
        mock_db.create_or_update_store = AsyncMock(return_value=None)
        response = client.get("/auth/google/callback?code=valid_code", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/line-settings"


# --- T9: JWT-gated admin routes ---

def test_dashboard_access_denied_no_jwt(client):
    """No JWT cookie → redirect to /admin."""
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin"


def test_dashboard_access_allowed_with_jwt(client, mock_db):
    """Valid JWT cookie → dashboard renders with store data."""
    token = _make_admin_jwt()
    client.cookies.set("admin_token", token)
    mock_db.get_all_reservations = AsyncMock(return_value=[
        {"id": "1", "date": "2025-12-25", "time": "12:00", "name": "Test User", "phone": "0912345678", "pax": 2}
    ])
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "Reservation Management" in response.text
    assert "Test User" in response.text


def test_dashboard_access_denied_invalid_jwt(client):
    """Tampered JWT → redirect to /admin."""
    client.cookies.set("admin_token", "not.a.valid.jwt")
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin"


def test_cleanup_route_with_jwt(client, mock_db):
    """Cleanup route accepts valid JWT."""
    token = _make_admin_jwt()
    client.cookies.set("admin_token", token)
    response = client.post("/admin/cleanup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"
    mock_db.delete_past_reservations.assert_called_once()


# --- Webhook / callback tests (T3) ---

def _make_signature(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


WEBHOOK_BODY = json.dumps({
    "destination": "Utest_store_id",
    "events": []
}).encode()

STORE_SECRET = "my_channel_secret"
STORE_TOKEN = "my_channel_access_token"


@pytest.fixture
def mock_db_webhook():
    with patch("app.db") as mock:
        mock.get_store_by_destination = AsyncMock(return_value={
            "store_id": "store_abc",
            "line_bot_id": "Utest_store_id",
        })
        mock.get_store_credentials = AsyncMock(return_value=(STORE_TOKEN, STORE_SECRET))
        yield mock


def test_callback_correct_signature(client, mock_db_webhook):
    sig = _make_signature(STORE_SECRET, WEBHOOK_BODY)
    response = client.post(
        "/callback",
        content=WEBHOOK_BODY,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200


def test_callback_wrong_signature(client, mock_db_webhook):
    response = client.post(
        "/callback",
        content=WEBHOOK_BODY,
        headers={"X-Line-Signature": "bad_sig", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_callback_unknown_destination(client):
    with patch("app.db") as mock:
        mock.get_store_by_destination = AsyncMock(return_value=None)
        response = client.post(
            "/callback",
            content=WEBHOOK_BODY,
            headers={"X-Line-Signature": "any", "Content-Type": "application/json"},
        )
    assert response.status_code == 404


# --- T7: store_id wired through to process() ---

def _make_message_event_body(destination: str, user_id: str, text: str) -> bytes:
    import json
    return json.dumps({
        "destination": destination,
        "events": [{
            "type": "message",
            "replyToken": "reply-token-123",
            "source": {"type": "user", "userId": user_id},
            "message": {"type": "text", "id": "msg-1", "text": text},
            "webhookEventId": "evt-unique-1",
            "timestamp": 0,
        }]
    }).encode()


def test_callback_passes_store_id_to_process(client):
    """process() must be called with the store_id extracted from the webhook destination."""
    body = _make_message_event_body("Utest_store_id", "Uuser123", "我想訂位")
    sig = _make_signature(STORE_SECRET, body)

    with patch("app.db") as mock_db, \
         patch("app.langgraph_agent") as mock_agent:

        mock_db.get_store_by_destination = AsyncMock(return_value={
            "store_id": "store_abc",
            "line_bot_id": "Utest_store_id",
        })
        mock_db.get_store_credentials = AsyncMock(return_value=(STORE_TOKEN, STORE_SECRET))
        mock_agent.process = AsyncMock(return_value="好的，請問您想預約哪天？")

        with patch("app.LineBotApi"), patch("app.WebhookParser") as MockParser:
            from unittest.mock import MagicMock
            from linebot.models import MessageEvent, TextMessage, Source
            mock_event = MagicMock(spec=MessageEvent)
            mock_event.message = MagicMock(spec=TextMessage)
            mock_event.message.text = "我想訂位"
            mock_event.source = MagicMock()
            mock_event.source.user_id = "Uuser123"
            mock_event.webhook_event_id = "evt-unique-1"
            MockParser.return_value.parse = MagicMock(return_value=[mock_event])

            response = client.post(
                "/callback",
                content=body,
                headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_agent.process.assert_called_once()
    call_kwargs = mock_agent.process.call_args
    assert call_kwargs.kwargs.get("store_id") == "store_abc" or \
           (len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "store_abc")


# --- T15: /admin/settings smoke test ---

def test_settings_overview_requires_auth(client):
    """GET /admin/settings without JWT redirects to /admin."""
    response = client.get("/admin/settings", follow_redirects=False)
    assert response.status_code in (302, 303, 307)


def test_settings_overview_renders_with_auth(client, mock_db):
    """GET /admin/settings with valid JWT returns 200."""
    mock_db.get_menu = AsyncMock(return_value=[
        {"id": "1", "name": "按摩", "duration": 60, "price": 1200}
    ])
    mock_db.get_business_hours = AsyncMock(return_value={
        "Monday": {"open": "09:00", "close": "18:00", "closed": False}
    })
    mock_db.get_special_closures = AsyncMock(return_value=[])
    mock_db.get_notification_settings = AsyncMock(return_value={"admin_ids": []})
    mock_db.get_store = AsyncMock(return_value={"line_bot_id": "Ctest"})
    mock_db.get_employees = AsyncMock(return_value=[])

    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "按摩" in response.text
