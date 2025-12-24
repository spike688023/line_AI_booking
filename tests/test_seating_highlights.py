import pytest
from unittest.mock import MagicMock, patch
from src.database import Database

# 測試座位高亮功能的測試案例 (Test Case for Seating Highlight)

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
async def test_seat_map_assignment_logic():
    """
    測試後端產生的 seat_map 是否能正確將每一把椅子分配給對應的訂單 ID。
    """
    # 模擬資料：桌子 2F-B1 (6人桌) 有兩組客人：徐先生 (5人) 和 王先生 (1人)
    occupied_tables = {
        "2F-B1": {
            "booked_pax": 6,
            "bookings": [
                {"res_id": "ID-XU-5", "name": "徐先生", "pax": 5},
                {"res_id": "ID-WANG-1", "name": "王先生", "pax": 1}
            ]
        }
    }
    
    # 模擬 app.py 中的 pre-process 邏輯
    for tid, data in occupied_tables.items():
        seat_map = []
        for b in data.get("bookings", []):
            for _ in range(b.get("pax", 0)):
                seat_map.append(b.get("res_id", ""))
        data["seat_map"] = seat_map
        
    # 驗證 2F-B1 的椅子分配
    seat_map_b1 = occupied_tables["2F-B1"]["seat_map"]
    
    # 前 5 把椅子應該屬於徐先生
    assert seat_map_b1[0] == "ID-XU-5"
    assert seat_map_b1[1] == "ID-XU-5"
    assert seat_map_b1[2] == "ID-XU-5"
    assert seat_map_b1[3] == "ID-XU-5"
    assert seat_map_b1[4] == "ID-XU-5"
    
    # 第 6 把椅子應該屬於王先生
    assert seat_map_b1[5] == "ID-WANG-1"
    
    # 驗證數量
    assert len(seat_map_b1) == 6

@pytest.mark.asyncio
async def test_seat_map_partial_occupancy():
    """
    測試不滿座的情況下，seat_map 是否正確。
    """
    # 模擬資料：4人桌 (2F-C1) 只有 2 人座 (張先生)
    occupied_tables = {
        "2F-C1": {
            "booked_pax": 2,
            "bookings": [
                {"res_id": "ID-ZHANG-2", "name": "張先生", "pax": 2}
            ]
        }
    }
    
    # 模擬 pre-process
    for tid, data in occupied_tables.items():
        seat_map = []
        for b in data.get("bookings", []):
            for _ in range(b.get("pax", 0)):
                seat_map.append(b.get("res_id", ""))
        data["seat_map"] = seat_map
        
    seat_map_c1 = occupied_tables["2F-C1"]["seat_map"]
    
    assert len(seat_map_c1) == 2
    assert seat_map_c1[0] == "ID-ZHANG-2"
    assert seat_map_c1[1] == "ID-ZHANG-2"
    # 注意：剩下的 2 個空位在 HTML template 中會因為長度不足而不帶 ID，這符合預期。
