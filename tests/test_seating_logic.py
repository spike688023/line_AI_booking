import pytest
from unittest.mock import MagicMock, patch
from src.database import Database

@pytest.fixture
def mock_firestore():
    with patch("src.database.firestore.Client") as mock_client:
        yield mock_client

@pytest.fixture
def db(mock_firestore):
    with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "dummy-project"}):
        database = Database()
        database.client = mock_firestore.return_value
        return database

@pytest.mark.asyncio
async def test_get_daily_occupied_tables_empty(db):
    # Mock empty occupancy
    mock_slot_ref = db.client.collection.return_value.document.return_value
    mock_snapshot = MagicMock()
    mock_snapshot.exists = False
    mock_slot_ref.get.return_value = mock_snapshot
    
    occupancy = await db.get_daily_occupied_tables("2025-12-25")
    assert occupancy == {}

@pytest.mark.asyncio
async def test_check_availability_logic(db):
    # Mock occupancy: Table 2F-A1 (2p) is full, 2F-B1 (6p) has 4 seats taken (2 left)
    mock_slot_ref = db.client.collection.return_value.document.return_value
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "occupancy": {
            "2F-A1": {"booked_pax": 2},
            "2F-B1": {"booked_pax": 4}
        }
    }
    mock_slot_ref.get.return_value = mock_snapshot
    
    # Can we fit 2 people? Yes, in 2F-B1 (remaining 2) or any other empty table.
    assert await db.check_availability("2025-12-25", "18:00", 2) is True
    
    # Can we fit 6 people? Only if a 6p table is empty. 
    # Let's mock ALL tables full except 2F-B1 which has 4/6.
    full_occupancy = {tid: {"booked_pax": conf["capacity"]} for tid, conf in db.TABLE_CONFIG.items()}
    full_occupancy["2F-B1"] = {"booked_pax": 4}
    mock_snapshot.to_dict.return_value = {"occupancy": full_occupancy}
    
    # 6 people won't fit anywhere
    assert await db.check_availability("2025-12-25", "18:00", 6) is False
    # 2 people will fit in 2F-B1
    assert await db.check_availability("2025-12-25", "18:00", 2) is True

@pytest.mark.asyncio
async def test_create_reservation_allocation(db):
    # Mock transaction
    mock_transaction = MagicMock()
    db.client.transaction.return_value = mock_transaction
    
    # Mock daily_slots snapshot (empty)
    mock_slot_snapshot = MagicMock()
    mock_slot_snapshot.exists = False
    
    # Mock transactional decorator to just call the function
    with patch("google.cloud.firestore.transactional", lambda x: x):
        # We need to mock the internal create_in_transaction call or the way it's called
        # Since Database.create_reservation uses @firestore.transactional, we mock the client's transaction
        
        # Mock the snapshot return inside the transaction
        mock_slot_ref = db.client.collection.return_value.document.return_value
        mock_slot_ref.get.return_value = mock_slot_snapshot
        
        # 1. Book 2 people. Should pick a 2p table (e.g., 2F-A1)
        result = await db.create_reservation("u1", "2025-12-25", "12:00", 2, "User1", "123")
        res_id, table_id = result.split("|")
        assert "2F-A" in table_id # Smallest table for 2p
        
        # 2. Mock that ALL 2p tables (2F and 3F) are full, book 2 people. Should pick a 4p table.
        mock_slot_snapshot.exists = True
        all_2p_full = {tid: {"booked_pax": 2} for tid, conf in db.TABLE_CONFIG.items() if conf["capacity"] == 2}
        mock_slot_snapshot.to_dict.return_value = {"occupancy": all_2p_full}
        
        result = await db.create_reservation("u2", "2025-12-25", "13:00", 2, "User2", "456")
        _, table_id = result.split("|")
        # Should pick a 4p table (2F-C, 2F-D, 3F-G, 3F-H, 3F-I)
        assert db.TABLE_CONFIG[table_id]["capacity"] == 4

@pytest.mark.asyncio
async def test_shared_table_logic(db):
    # Mock transaction
    mock_transaction = MagicMock()
    db.client.transaction.return_value = mock_transaction
    
    # Mock 6p table (2F-B1) already has 2 people. Book another 2 people.
    mock_slot_snapshot = MagicMock()
    mock_slot_snapshot.exists = True
    mock_slot_snapshot.to_dict.return_value = {
        "occupancy": {
            "2F-B1": {"booked_pax": 2, "bookings": [{"name": "Old", "pax": 2}]}
        }
    }
    
    with patch("google.cloud.firestore.transactional", lambda x: x):
        mock_slot_ref = db.client.collection.return_value.document.return_value
        mock_slot_ref.get.return_value = mock_slot_snapshot
        
        # 1. Book 2 people. Should prefer an empty 2p table over sharing a 6p table (if available)
        result = await db.create_reservation("u3", "2025-12-25", "14:00", 2, "User3", "789")
        _, table_id = result.split("|")
        assert db.TABLE_CONFIG[table_id]["capacity"] == 2 
        
        # 2. Now mock ALL 2p and 4p tables are full. B1 (6p) has 2/6.
        full_occupancy = {tid: {"booked_pax": conf["capacity"]} for tid, conf in db.TABLE_CONFIG.items() if conf["capacity"] < 6}
        full_occupancy["2F-B1"] = {"booked_pax": 2, "bookings": []} # Added bookings key to avoid previous error
        mock_slot_snapshot.to_dict.return_value = {"occupancy": full_occupancy}
        
        result = await db.create_reservation("u4", "2025-12-25", "15:00", 2, "User4", "000")
        _, table_id = result.split("|")
        assert table_id == "2F-B1" # Must share the 6p table because others are full
