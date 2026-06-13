import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import os
os.environ["GOOGLE_CLOUD_PROJECT"] = "dummy"


# ── check_availability ────────────────────────────────────────────────────────

class TestCheckAvailability:
    @pytest.mark.asyncio
    async def test_available(self):
        with patch("src.tools.db") as mock_db:
            mock_db.check_availability = AsyncMock(return_value=True)
            from src.tools import check_availability
            result = await check_availability.ainvoke({
                "store_id": "store_abc",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 2,
            })
        assert result == {"available": True, "error": None}

    @pytest.mark.asyncio
    async def test_not_available(self):
        with patch("src.tools.db") as mock_db:
            mock_db.check_availability = AsyncMock(return_value=False)
            from src.tools import check_availability
            result = await check_availability.ainvoke({
                "store_id": "store_abc",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 20,
            })
        assert result == {"available": False, "error": None}


# ── execute_booking ───────────────────────────────────────────────────────────

class TestExecuteBooking:
    @pytest.mark.asyncio
    async def test_success_returns_dict(self):
        mock_line_api = MagicMock()
        mock_line_api.push_message = MagicMock()

        with patch("src.tools.db") as mock_db:
            mock_db.create_reservation = AsyncMock(return_value="res-001|T1A")
            from src.tools import execute_booking
            result = await execute_booking.ainvoke({
                "store_id": "store_abc",
                "user_id": "U123",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 2,
                "name": "王小明",
                "phone": "0912345678",
                "floor": 2,
                "allow_split": False,
                "line_bot_api": mock_line_api,
            })

        assert result["success"] is True
        assert result["reservation_id"] == "res-001"
        assert result["tables"] == "T1A"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_success_sends_flex_message(self):
        mock_line_api = MagicMock()
        mock_line_api.push_message = MagicMock()

        with patch("src.tools.db") as mock_db:
            mock_db.create_reservation = AsyncMock(return_value="res-001|T1A")
            from src.tools import execute_booking
            await execute_booking.ainvoke({
                "store_id": "store_abc",
                "user_id": "U123",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 2,
                "name": "王小明",
                "phone": "0912345678",
                "floor": 2,
                "allow_split": False,
                "line_bot_api": mock_line_api,
            })

        mock_line_api.push_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_overbooked_returns_error(self):
        mock_line_api = MagicMock()

        with patch("src.tools.db") as mock_db:
            mock_db.create_reservation = AsyncMock(side_effect=Exception("overbooked"))
            from src.tools import execute_booking
            result = await execute_booking.ainvoke({
                "store_id": "store_abc",
                "user_id": "U123",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 100,
                "name": "王小明",
                "phone": "0912345678",
                "floor": None,
                "allow_split": False,
                "line_bot_api": mock_line_api,
            })

        assert result["success"] is False
        assert result["error"] == "該時段已客滿"
        mock_line_api.push_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_flex_message_without_api(self):
        """execute_booking still returns success when line_bot_api is None."""
        with patch("src.tools.db") as mock_db:
            mock_db.create_reservation = AsyncMock(return_value="res-002|T2B")
            from src.tools import execute_booking
            result = await execute_booking.ainvoke({
                "store_id": "store_abc",
                "user_id": "U123",
                "date": "2026-07-01",
                "time": "18:00",
                "pax": 2,
                "name": "王小明",
                "phone": "0912345678",
                "floor": None,
                "allow_split": False,
            })

        assert result["success"] is True
