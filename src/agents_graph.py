"""
LangGraph ReAct agent for the Coffee Shop booking system.

Single agent_node ↔ tools_node loop (T5).
Dynamic system prompt per store (T6).
"""

import asyncio
import logging
import os
from typing import Dict, Any, Literal

from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_google_genai import ChatGoogleGenerativeAI

from src.database import db
from src.tools import check_availability, execute_booking

load_dotenv()
logger = logging.getLogger(__name__)

_TOOLS = [check_availability, execute_booking]

_BASE_PROMPT = """\
你是 AI 訂位助理。

【你的工作】
- 幫助用戶完成訂位（使用 check_availability 確認空位，再用 execute_booking 建立訂位）
- 回答店家相關問題

【樓層說明】
- 1樓：限時 90 分鐘
- 2樓：不限時，適合聊天
- 3樓：不限時，安靜區（禁止聊天）

【回應規則】
1. 使用繁體中文
2. 簡潔有禮，一次只問一個問題
3. 訂位資訊（日期、時間、人數、姓名、電話）收集齊全後才呼叫工具
4. 工具回傳 success=False 時，告知用戶失敗原因，不要說訂位成功
"""


def build_system_prompt(menu: list, hours: dict, table_config: dict, custom_prompt: str) -> str:  # noqa: ARG001 (table_config reserved for future floor-capacity display)
    """Compose a store-specific system prompt from DB data."""
    parts = [_BASE_PROMPT]

    if menu:
        items = menu[:20]  # cap at 20 to avoid token bloat
        lines = [f"  - {i.get('name')} NT${i.get('price')} ({i.get('category', '')})" for i in items]
        parts.append("【菜單】\n" + "\n".join(lines))

    if hours:
        day_lines = []
        for day, cfg in hours.items():
            if cfg.get("closed"):
                day_lines.append(f"  {day}: 休息")
            else:
                day_lines.append(f"  {day}: {cfg.get('open')} – {cfg.get('close')}")
        parts.append("【營業時間】\n" + "\n".join(day_lines))

    if custom_prompt:
        parts.append(f"【店家特別指示】\n{custom_prompt}")

    return "\n\n".join(parts)


def _make_thread_id(store_id: str, user_id: str) -> str:
    return f"{store_id}_{user_id}"


def detect_language(text: str) -> Literal["zh-TW", "en"]:
    try:
        text.encode("ascii")
        return "en"
    except UnicodeEncodeError:
        return "zh-TW"


class LangGraphAgent:
    def __init__(self):
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        self.graph = create_react_agent(
            model=llm,
            tools=_TOOLS,
            prompt=_BASE_PROMPT,
            checkpointer=MemorySaver(),
        )
        logger.info("[LangGraphAgent] ReAct agent initialized")

    async def _load_system_prompt(self, store_id: str) -> str:
        """Load store-specific context from DB and build a dynamic system prompt."""
        if not store_id:
            return _BASE_PROMPT
        try:
            menu, hours, table_config, store = await asyncio.gather(
                db.get_menu(store_id),
                db.get_business_hours(store_id),
                db.get_table_config(store_id),
                db.get_store(store_id),
            )
            custom_prompt = (store or {}).get("custom_prompt", "")
            return build_system_prompt(menu, hours, table_config, custom_prompt)
        except Exception as e:
            logger.error(f"[LangGraphAgent] failed to load system prompt for {store_id}: {e}")
            return _BASE_PROMPT

    async def process(self, user_message: str, context: Dict[str, Any] = None, store_id: str = "") -> str:
        user_id = (context or {}).get("user_id", "unknown")
        thread_id = _make_thread_id(store_id, user_id)
        system_prompt = await self._load_system_prompt(store_id)
        config = {"configurable": {"thread_id": thread_id}}

        try:
            result = await self.graph.ainvoke(
                {"messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ]},
                config=config,
            )
            return result["messages"][-1].content
        except Exception as e:
            logger.error(f"[LangGraphAgent] process error: {e}")
            return "抱歉，系統發生錯誤。" if detect_language(user_message) == "zh-TW" else "Sorry, system error."


# Singleton
langgraph_agent = LangGraphAgent()
