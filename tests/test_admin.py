import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
import os
import hmac
import hashlib
import base64
import json

# Set dummy env vars before importing app to avoid errors
os.environ["GOOGLE_CLOUD_PROJECT"] = "dummy"

from app import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_db():
    with patch("app.db") as mock:
        mock.get_all_reservations = AsyncMock(return_value=[
            {"id": "1", "date": "2025-12-25", "time": "12:00", "name": "Test User", "phone": "0912345678", "pax": 2}
        ])
        mock.delete_past_reservations = AsyncMock(return_value=5)
        mock.delete_reservation = AsyncMock(return_value=True)
        mock.modify_reservation = AsyncMock(return_value="success")
        yield mock

def test_admin_login_page(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Admin Login" in response.text

def test_admin_login_success(client):
    # Default password is admin123
    response = client.post("/admin/login", data={"password": "admin123"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"
    assert "admin_session=logged_in" in response.headers["set-cookie"]

def test_admin_login_failure(client):
    response = client.post("/admin/login", data={"password": "wrongpassword"})
    assert response.status_code == 200
    assert "Invalid Password" in response.text

def test_dashboard_access_denied(client):
    response = client.get("/admin/dashboard", follow_redirects=False)
    # RedirectResponse default is 307
    assert response.status_code == 307 
    assert response.headers["location"] == "/admin"

def test_dashboard_access_allowed(client, mock_db):
    # Set cookie manually
    client.cookies.set("admin_session", "logged_in")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "Reservation Management" in response.text
    assert "Test User" in response.text # Check if mock data is rendered

def test_cleanup_route(client, mock_db):
    client.cookies.set("admin_session", "logged_in")
    response = client.post("/admin/cleanup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"

    # Verify DB method was called
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
