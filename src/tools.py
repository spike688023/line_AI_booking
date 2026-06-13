import logging
from typing import Annotated, Optional, Any

from langchain_core.tools import tool, InjectedToolArg
from linebot.models import FlexSendMessage

from src.database import db

logger = logging.getLogger(__name__)


def _build_booking_flex(reservation_id: str, date: str, time: str, pax: int, name: str, tables: str) -> FlexSendMessage:
    return FlexSendMessage(
        alt_text="訂位確認",
        contents={
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "訂位確認 ✅", "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"姓名：{name}"},
                    {"type": "text", "text": f"日期：{date}"},
                    {"type": "text", "text": f"時間：{time}"},
                    {"type": "text", "text": f"人數：{pax} 位"},
                    {"type": "text", "text": f"桌號：{tables}"},
                    {"type": "text", "text": f"訂位編號：{reservation_id[:8]}"},
                ],
            },
        },
    )


@tool
async def check_availability(store_id: str, date: str, time: str, pax: int) -> dict:
    """Check if a time slot has enough capacity for the requested party size."""
    try:
        available = await db.check_availability(store_id, date, time, pax)
        return {"available": available, "error": None}
    except Exception as e:
        logger.error(f"check_availability error: {e}")
        return {"available": False, "error": str(e)}


@tool
async def execute_booking(
    store_id: str,
    user_id: str,
    date: str,
    time: str,
    pax: int,
    name: str,
    phone: str,
    floor: Optional[int],
    allow_split: bool,
    line_bot_api: Annotated[Optional[Any], InjectedToolArg] = None,
) -> dict:
    """Create a reservation and send a LINE Flex Message confirmation to the user."""
    try:
        result = await db.create_reservation(
            store_id, user_id, date, time, pax, name, phone, floor, allow_split
        )
        parts = result.split("|", 1)
        reservation_id = parts[0]
        tables = parts[1] if len(parts) > 1 else ""

        if line_bot_api:
            try:
                flex = _build_booking_flex(reservation_id, date, time, pax, name, tables)
                line_bot_api.push_message(user_id, flex)
            except Exception as e:
                logger.error(f"Failed to send Flex Message: {e}")

        return {"success": True, "reservation_id": reservation_id, "tables": tables, "error": None}
    except Exception as e:
        error_msg = str(e)
        if "overbooked" in error_msg:
            return {"success": False, "reservation_id": None, "tables": None, "error": "該時段已客滿"}
        logger.error(f"execute_booking error: {e}")
        return {"success": False, "reservation_id": None, "tables": None, "error": error_msg}
