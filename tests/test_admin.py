import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
import os

# Set dummy env vars before importing app to avoid errors
os.environ["LINE_CHANNEL_SECRET"] = "dummy"
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "dummy"
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
