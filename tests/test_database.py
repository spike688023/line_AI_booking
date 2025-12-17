import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.database import Database

@pytest.fixture
def mock_firestore():
    with patch("src.database.firestore.Client") as mock_client:
        yield mock_client

@pytest.fixture
def db(mock_firestore):
    # Ensure env var is set so client initializes
    with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "dummy-project"}):
        database = Database()
        # Manually set the client to the mock instance returned by the class constructor
        database.client = mock_firestore.return_value
        return database

@pytest.mark.asyncio
async def test_check_availability(db):
    # Mock behavior: Always return True for now (as per current implementation)
    is_available = await db.check_availability("2025-12-25", "18:00", 2)
    assert is_available is True

@pytest.mark.asyncio
async def test_create_reservation(db):
    # Setup mock
    mock_collection = db.client.collection.return_value
    mock_doc_ref = mock_collection.document.return_value
    
    # Execute
    res_id = await db.create_reservation(
        user_id="user123",
        date="2025-12-25",
        time="18:00",
        pax=2,
        name="Test User",
        phone="0912345678"
    )
    
    # Verify
    assert res_id is not None
    # Verify collection("reservations") was called
    db.client.collection.assert_called_with("reservations")
    # Verify set() was called with correct data
    mock_doc_ref.set.assert_called_once()
    call_args = mock_doc_ref.set.call_args[0][0]
    assert call_args["user_id"] == "user123"
    assert call_args["date"] == "2025-12-25"
    assert call_args["status"] == "confirmed"

@pytest.mark.asyncio
async def test_get_user_reservations(db):
    # Setup mock data
    mock_stream = MagicMock()
    
    # Create mock documents
    doc1 = MagicMock()
    doc1.id = "res1"
    doc1.to_dict.return_value = {"date": "2099-12-31", "time": "12:00", "user_id": "user123"} # Future
    
    doc2 = MagicMock()
    doc2.id = "res2"
    doc2.to_dict.return_value = {"date": "2000-01-01", "time": "12:00", "user_id": "user123"} # Past
    
    # Configure query return
    mock_collection = db.client.collection.return_value
    mock_query = mock_collection.where.return_value
    mock_query.stream.return_value = [doc1, doc2]
    
    # Execute (default include_past=False)
    reservations = await db.get_user_reservations("user123", include_past=False)
    
    # Verify
    assert len(reservations) == 1
    assert reservations[0]["id"] == "res1" # Only future reservation returned

@pytest.mark.asyncio
async def test_modify_reservation_success(db):
    # Setup mock
    mock_doc_ref = db.client.collection.return_value.document.return_value
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_snapshot.to_dict.return_value = {"user_id": "user123", "pax": 2}
    mock_doc_ref.get.return_value = mock_doc_snapshot
    
    # Execute
    result = await db.modify_reservation("res1", "2025-12-26", "19:00", "user123")
    
    # Verify
    assert result == "success"
    mock_doc_ref.update.assert_called_once()

@pytest.mark.asyncio
async def test_modify_reservation_permission_denied(db):
    # Setup mock
    mock_doc_ref = db.client.collection.return_value.document.return_value
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_snapshot.to_dict.return_value = {"user_id": "other_user", "pax": 2} # Different user
    mock_doc_ref.get.return_value = mock_doc_snapshot
    
    # Execute
    result = await db.modify_reservation("res1", "2025-12-26", "19:00", "user123")
    
    # Verify
    assert result == "permission_denied"
    mock_doc_ref.update.assert_not_called()
