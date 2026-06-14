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


# ============================================================
# T12: Menu routes pass price field
# ============================================================

def test_menu_add_route_passes_price_to_db(client, mock_db):
    """POST /admin/menu/add must forward price to db.add_menu_item."""
    mock_db.add_menu_item = AsyncMock(return_value="new-id")
    token = _make_admin_jwt()
    response = client.post(
        "/admin/menu/add",
        data={"name": "深層按摩", "duration": "90", "price": "1800"},
        cookies={"admin_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    mock_db.add_menu_item.assert_called_once_with("store_abc", "深層按摩", 90, 1800)


def test_menu_update_route_passes_price_to_db(client, mock_db):
    """POST /admin/menu/update/{id} must include price in the update dict."""
    mock_db.update_menu_item = AsyncMock(return_value=True)
    token = _make_admin_jwt()
    response = client.post(
        "/admin/menu/update/item123",
        data={"name": "頭部按摩", "duration": "30", "price": "600"},
        cookies={"admin_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    mock_db.update_menu_item.assert_called_once_with(
        "store_abc", "item123", {"name": "頭部按摩", "duration": 30, "price": 600}
    )


def test_menu_dashboard_shows_price_column(client, mock_db):
    """GET /admin/menu must render the price column and value."""
    mock_db.get_menu = AsyncMock(return_value=[
        {"id": "1", "name": "全身按摩", "duration": 60, "price": 1200}
    ])
    token = _make_admin_jwt()
    response = client.get("/admin/menu", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "價格" in response.text
    assert "1200" in response.text


# ============================================================
# T14: Employee admin routes
# ============================================================

def test_employees_list_renders_with_auth(client, mock_db):
    """GET /admin/employees returns 200 and shows employee table."""
    mock_db.get_employees = AsyncMock(return_value=[
        {"id": "e1", "name": "小美", "schedule": {
            "Monday": {"start": "09:00", "end": "17:00", "off": False},
            "Tuesday": {"start": "09:00", "end": "17:00", "off": True},
        }}
    ])
    token = _make_admin_jwt()
    response = client.get("/admin/employees", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "小美" in response.text
    assert "員工排班" in response.text


def test_employees_add_calls_db_and_redirects(client, mock_db):
    """POST /admin/employees/add must call add_employee and redirect."""
    mock_db.add_employee = AsyncMock(return_value="emp_new")
    token = _make_admin_jwt()
    form = {
        "name": "小偉",
        "Monday_start": "09:00", "Monday_end": "17:00",
        "Tuesday_start": "09:00", "Tuesday_end": "17:00", "Tuesday_off": "on",
        "Wednesday_start": "09:00", "Wednesday_end": "17:00",
        "Thursday_start": "09:00", "Thursday_end": "17:00",
        "Friday_start": "09:00", "Friday_end": "17:00",
        "Saturday_start": "10:00", "Saturday_end": "18:00",
        "Sunday_start": "10:00", "Sunday_end": "18:00", "Sunday_off": "on",
    }
    response = client.post(
        "/admin/employees/add",
        data=form,
        cookies={"admin_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    mock_db.add_employee.assert_called_once()
    call_args = mock_db.add_employee.call_args
    assert call_args[0][0] == "store_abc"
    assert call_args[0][1] == "小偉"
    schedule = call_args[0][2]
    assert schedule["Tuesday"]["off"] is True
    assert schedule["Monday"]["off"] is False


def test_employees_delete_calls_db_and_redirects(client, mock_db):
    """POST /admin/employees/delete/{id} must call delete_employee and redirect."""
    mock_db.delete_employee = AsyncMock(return_value=True)
    token = _make_admin_jwt()
    response = client.post(
        "/admin/employees/delete/emp_abc",
        cookies={"admin_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    mock_db.delete_employee.assert_called_once_with("store_abc", "emp_abc")


def test_employees_requires_auth(client):
    """GET /admin/employees without JWT must redirect."""
    response = client.get("/admin/employees", follow_redirects=False)
    assert response.status_code in (302, 303, 307)


# ============================================================
# T15/T16: Settings page content and nav links
# ============================================================

def _settings_mock_db(mock_db, **overrides):
    defaults = {
        "get_menu": AsyncMock(return_value=[]),
        "get_business_hours": AsyncMock(return_value={}),
        "get_special_closures": AsyncMock(return_value=[]),
        "get_notification_settings": AsyncMock(return_value={"admin_ids": []}),
        "get_store": AsyncMock(return_value={"line_bot_id": ""}),
        "get_employees": AsyncMock(return_value=[]),
    }
    defaults.update(overrides)
    for attr, val in defaults.items():
        setattr(mock_db, attr, val)


def test_settings_shows_price_in_menu_card(client, mock_db):
    """Settings page renders price in the services card."""
    _settings_mock_db(mock_db,
        get_menu=AsyncMock(return_value=[
            {"id": "1", "name": "腳底按摩", "duration": 60, "price": 900}
        ])
    )
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "腳底按摩" in response.text
    assert "900" in response.text


def test_settings_empty_menu_shows_hint(client, mock_db):
    """Settings page shows empty-hint when no services configured."""
    _settings_mock_db(mock_db)
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "尚未新增服務項目" in response.text


def test_settings_empty_employees_shows_hint(client, mock_db):
    """Settings page shows empty-hint when no employees configured."""
    _settings_mock_db(mock_db)
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert response.status_code == 200
    assert "尚未設定員工班表" in response.text


def test_settings_closure_tags_shown(client, mock_db):
    """Settings page renders closure dates as tags."""
    _settings_mock_db(mock_db,
        get_special_closures=AsyncMock(return_value=["2026-01-01", "2026-02-08"])
    )
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert "2026-01-01" in response.text
    assert "2026-02-08" in response.text


def test_settings_empty_closures_shows_hint(client, mock_db):
    """Settings page shows hint when no closures."""
    _settings_mock_db(mock_db)
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert "尚未設定公休日" in response.text


def test_settings_line_bot_connected_status(client, mock_db):
    """Settings page shows ✅ when line_bot_id is present."""
    _settings_mock_db(mock_db,
        get_store=AsyncMock(return_value={"line_bot_id": "Cabc123"})
    )
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert "✅" in response.text
    assert "Cabc123" in response.text


def test_settings_line_bot_disconnected_status(client, mock_db):
    """Settings page shows ❌ when line_bot_id is empty."""
    _settings_mock_db(mock_db,
        get_store=AsyncMock(return_value={"line_bot_id": ""})
    )
    token = _make_admin_jwt()
    response = client.get("/admin/settings", cookies={"admin_token": token})
    assert "❌" in response.text


def test_settings_nav_link_in_menu_dashboard(client, mock_db):
    """menu_dashboard.html must contain a link to /admin/settings."""
    mock_db.get_menu = AsyncMock(return_value=[])
    token = _make_admin_jwt()
    response = client.get("/admin/menu", cookies={"admin_token": token})
    assert "/admin/settings" in response.text


def test_settings_nav_link_in_hours_dashboard(client, mock_db):
    """hours_dashboard.html must contain a link to /admin/settings."""
    mock_db.get_business_hours = AsyncMock(return_value={})
    mock_db.get_special_closures = AsyncMock(return_value=[])
    token = _make_admin_jwt()
    response = client.get("/admin/hours", cookies={"admin_token": token})
    assert "/admin/settings" in response.text


def test_settings_nav_link_in_notifications_dashboard(client, mock_db):
    """notifications_dashboard.html must contain a link to /admin/settings."""
    mock_db.get_notification_settings = AsyncMock(return_value={"admin_ids": []})
    token = _make_admin_jwt()
    response = client.get("/admin/notifications", cookies={"admin_token": token})
    assert "/admin/settings" in response.text
