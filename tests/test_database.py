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

# ============================================================
# T1: New store-level methods
# ============================================================

@pytest.mark.asyncio
async def test_get_store_by_destination_found(db):
    mock_doc = MagicMock()
    mock_doc.id = "store_abc"
    mock_doc.to_dict.return_value = {"line_bot_id": "Uabc", "name": "Test Café"}
    db.client.collection.return_value.where.return_value.limit.return_value.stream.return_value = iter([mock_doc])

    result = await db.get_store_by_destination("Uabc")

    assert result is not None
    assert result["store_id"] == "store_abc"
    assert result["name"] == "Test Café"


@pytest.mark.asyncio
async def test_get_store_by_destination_not_found(db):
    db.client.collection.return_value.where.return_value.limit.return_value.stream.return_value = iter([])

    result = await db.get_store_by_destination("Uunknown")

    assert result is None


@pytest.mark.asyncio
async def test_get_store_credentials(db):
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "channel_access_token": "token_xyz",
        "channel_secret": "secret_abc"
    }
    db.client.collection.return_value.document.return_value.get.return_value = mock_doc

    token, secret = await db.get_store_credentials("store_abc")

    assert token == "token_xyz"
    assert secret == "secret_abc"


@pytest.mark.asyncio
async def test_get_table_config(db):
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "tables": {"2F-A1": {"capacity": 2, "floor": 2}},
        "total_capacity": 2
    }
    # stores/{store_id}/config/table_layout
    db.client.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = mock_doc

    result = await db.get_table_config("store_abc")

    assert result["total_capacity"] == 2
    assert "2F-A1" in result["tables"]


@pytest.mark.asyncio
async def test_get_table_config_missing(db):
    mock_doc = MagicMock()
    mock_doc.exists = False
    db.client.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = mock_doc

    result = await db.get_table_config("store_abc")

    assert result == {"tables": {}, "total_capacity": 0}


# ============================================================
# T2: store_id isolation tests (RED until database.py is rewritten)
# ============================================================

@pytest.mark.asyncio
async def test_get_menu_reads_store_subcollection(db):
    """get_menu must read from stores/{store_id}/menu/, not the global menu collection."""
    mock_item = MagicMock()
    mock_item.id = "item1"
    mock_item.to_dict.return_value = {"name": "Latte", "price": 100}
    db.client.collection.return_value.document.return_value.collection.return_value.stream.return_value = [mock_item]

    result = await db.get_menu("store_a")

    db.client.collection.assert_called_with("stores")
    db.client.collection.return_value.document.assert_called_with("store_a")
    assert len(result) == 1
    assert result[0]["name"] == "Latte"


@pytest.mark.asyncio
async def test_daily_slots_doc_id_includes_store_id(db):
    """get_daily_occupied_tables must read doc '{store_id}_{date}', not just '{date}'."""
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {"occupancy": {}}
    db.client.collection.return_value.document.return_value.get.return_value = mock_snap

    await db.get_daily_occupied_tables("store_a", "2026-01-01")

    db.client.collection.assert_called_with("daily_slots")
    db.client.collection.return_value.document.assert_called_with("store_a_2026-01-01")


# ============================================================
# T2: Updated existing tests (store_id as first argument)
# ============================================================

@pytest.mark.asyncio
async def test_check_availability(db):
    db.get_table_config = AsyncMock(return_value={
        "tables": {"T1": {"capacity": 4, "floor": 1}},
        "total_capacity": 4,
    })
    db.get_special_closures = AsyncMock(return_value=[])
    db.get_daily_occupied_tables = AsyncMock(return_value={})

    is_available = await db.check_availability("store123", "2099-12-25", "18:00", 2)
    assert is_available is True


@pytest.mark.asyncio
async def test_check_availability_fully_booked(db):
    db.get_table_config = AsyncMock(return_value={
        "tables": {"T1": {"capacity": 2, "floor": 1}},
        "total_capacity": 2,
    })
    db.get_special_closures = AsyncMock(return_value=[])
    db.get_daily_occupied_tables = AsyncMock(return_value={"T1": {"booked_pax": 2, "bookings": []}})

    is_available = await db.check_availability("store123", "2099-12-25", "18:00", 2)
    assert is_available is False


@pytest.mark.asyncio
async def test_create_reservation_no_client(db):
    db.client = None
    res_id = await db.create_reservation(
        store_id="store123",
        user_id="user123",
        date="2099-12-25",
        time="18:00",
        pax=2,
        name="Test User",
        phone="0912345678",
    )
    assert res_id == "mock-reservation-id"


@pytest.mark.asyncio
async def test_get_user_reservations(db):
    doc1 = MagicMock()
    doc1.id = "res1"
    doc1.to_dict.return_value = {"date": "2099-12-31", "time": "12:00", "user_id": "user123", "store_id": "store123"}

    doc2 = MagicMock()
    doc2.id = "res2"
    doc2.to_dict.return_value = {"date": "2000-01-01", "time": "12:00", "user_id": "user123", "store_id": "store123"}

    db.client.collection.return_value.where.return_value.where.return_value.stream.return_value = [doc1, doc2]

    reservations = await db.get_user_reservations("store123", "user123", include_past=False)

    assert len(reservations) == 1
    assert reservations[0]["id"] == "res1"


@pytest.mark.asyncio
async def test_modify_reservation_same_slot(db):
    """Same date/time as stored triggers the lightweight update path."""
    mock_doc_ref = db.client.collection.return_value.document.return_value
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {
        "user_id": "user123",
        "pax": 2,
        "date": "2025-12-26",
        "time": "19:00",
    }
    mock_doc_ref.get.return_value = mock_snap

    result = await db.modify_reservation("store123", "res1", "2025-12-26", "19:00", "user123")

    assert result == "success"
    # transaction.update() is used inside @firestore.transactional, not reservation_ref.update()
    db.client.transaction.return_value.update.assert_called_once()


@pytest.mark.asyncio
async def test_modify_reservation_permission_denied(db):
    mock_doc_ref = db.client.collection.return_value.document.return_value
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {"user_id": "other_user", "pax": 2, "date": "2025-12-26", "time": "19:00"}
    mock_doc_ref.get.return_value = mock_snap

    result = await db.modify_reservation("store123", "res1", "2025-12-26", "19:00", "user123")

    assert result == "permission_denied"
    mock_doc_ref.update.assert_not_called()


# ============================================================
# T13: Employee CRUD (mock-mode stubs)
# ============================================================

@pytest.mark.asyncio
async def test_get_employees_mock_mode():
    db_mock = Database()
    db_mock.client = None
    result = await db_mock.get_employees("store_x")
    assert result == []


@pytest.mark.asyncio
async def test_add_employee_mock_mode():
    db_mock = Database()
    db_mock.client = None
    emp_id = await db_mock.add_employee("store_x", "小美", {"Monday": {"start": "09:00", "end": "17:00", "off": False}})
    assert emp_id == "mock-id"


@pytest.mark.asyncio
async def test_update_employee_mock_mode():
    db_mock = Database()
    db_mock.client = None
    result = await db_mock.update_employee("store_x", "emp1", {"name": "Updated"})
    assert result is False


@pytest.mark.asyncio
async def test_delete_employee_mock_mode():
    db_mock = Database()
    db_mock.client = None
    result = await db_mock.delete_employee("store_x", "emp1")
    assert result is False


@pytest.mark.asyncio
async def test_get_employees_returns_id(db):
    mock_doc = MagicMock()
    mock_doc.id = "emp_abc"
    mock_doc.to_dict.return_value = {"name": "小偉", "schedule": {}}
    (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .stream.return_value
    ) = iter([mock_doc])

    result = await db.get_employees("store_x")

    assert len(result) == 1
    assert result[0]["id"] == "emp_abc"
    assert result[0]["name"] == "小偉"


# ============================================================
# T12: add_menu_item writes price to Firestore
# ============================================================

@pytest.mark.asyncio
async def test_add_menu_item_writes_price(db):
    """add_menu_item must include price in the Firestore document."""
    ref_mock = MagicMock()
    ref_mock.id = "item_xyz"
    (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    ) = ref_mock

    await db.add_menu_item("store1", "按摩", 60, price=1500)

    ref_mock.set.assert_called_once()
    written = ref_mock.set.call_args[0][0]
    assert written["name"] == "按摩"
    assert written["duration"] == 60
    assert written["price"] == 1500


@pytest.mark.asyncio
async def test_add_menu_item_price_defaults_to_zero(db):
    """add_menu_item price defaults to 0 when not supplied."""
    ref_mock = MagicMock()
    (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    ) = ref_mock

    await db.add_menu_item("store1", "洗髮", 30)

    written = ref_mock.set.call_args[0][0]
    assert written["price"] == 0


# ============================================================
# T13: Employee CRUD with Firestore mock
# ============================================================

@pytest.mark.asyncio
async def test_add_employee_calls_firestore(db):
    """add_employee writes name and schedule to Firestore."""
    ref_mock = MagicMock()
    ref_mock.id = "emp_new"
    (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    ) = ref_mock

    schedule = {"Monday": {"start": "09:00", "end": "17:00", "off": False}}
    emp_id = await db.add_employee("store1", "小美", schedule)

    ref_mock.set.assert_called_once()
    written = ref_mock.set.call_args[0][0]
    assert written["name"] == "小美"
    assert written["schedule"] == schedule
    assert emp_id == "emp_new"


@pytest.mark.asyncio
async def test_update_employee_calls_firestore(db):
    """update_employee calls Firestore .update() with the provided data."""
    doc_ref = (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    )

    result = await db.update_employee("store1", "emp_abc", {"name": "小偉"})

    doc_ref.update.assert_called_once_with({"name": "小偉"})
    assert result is True


@pytest.mark.asyncio
async def test_delete_employee_calls_firestore(db):
    """delete_employee calls Firestore .delete()."""
    doc_ref = (
        db.client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    )

    result = await db.delete_employee("store1", "emp_abc")

    doc_ref.delete.assert_called_once()
    assert result is True
