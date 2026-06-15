"""
Single Gemini agent with tool calling for the Coffee Shop booking system.
"""

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, Dict, Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.database import db
from src.tools import check_availability, execute_booking

load_dotenv()
logger = logging.getLogger(__name__)

_TOOLS = [check_availability, execute_booking]
_TOOL_MAP = {t.name: t for t in _TOOLS}

_BASE_PROMPT = """\
你是 AI 訂位助理。

【訂位流程】
一次詢問所有需要的資訊，有缺漏再補問：
  服務項目、人數、日期、時間、姓名、電話
資訊齊全後才呼叫工具。

【工具使用規則】
- check_availability：日期/時間/人數確認後立即呼叫，不可憑感覺回答有無空位
- execute_booking：check_availability 確認有空位後才呼叫，floor 傳 None
- 工具回傳 success=False：誠實告知客人失敗原因，禁止謊稱訂位成功

【回應規則】
1. 使用繁體中文
2. 親切有禮
3. 訂位完成後不需自行報訂位編號，系統會送出確認訊息
"""


def build_system_prompt(menu: list, hours: dict, table_config: dict, custom_prompt: str) -> str:  # noqa: ARG001
    parts = [_BASE_PROMPT]

    if menu:
        items = menu[:20]
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
        self._llm = None
        self._history: Dict[str, list] = defaultdict(list)

    @property
    def llm(self):
        if self._llm is None:
            base = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash-001",
                google_api_key=os.getenv("GOOGLE_API_KEY"),
            )
            self._llm = base.bind_tools(_TOOLS)
            logger.info("[Agent] initialized")
        return self._llm

    async def _load_system_prompt(self, store_id: str, user_id: str) -> str:
        base = _BASE_PROMPT
        if store_id:
            try:
                menu, hours, table_config, store = await asyncio.gather(
                    db.get_menu(store_id),
                    db.get_business_hours(store_id),
                    db.get_table_config(store_id),
                    db.get_store(store_id),
                )
                custom_prompt = (store or {}).get("custom_prompt", "")
                base = build_system_prompt(menu, hours, table_config, custom_prompt)
            except Exception as e:
                logger.error(f"[Agent] failed to load system prompt for {store_id}: {e}")

        # Inject runtime context so tools receive correct IDs from LLM
        return base + f"\n\n【系統資訊】\nstore_id: {store_id}\nuser_id: {user_id}"

    async def process(self, user_message: str, context: Dict[str, Any] = None, store_id: str = "") -> str:
        user_id = (context or {}).get("user_id", "unknown")
        thread_id = _make_thread_id(store_id, user_id)
        system_prompt = await self._load_system_prompt(store_id, user_id)

        history = self._history[thread_id]
        history.append(HumanMessage(content=user_message))

        messages = [SystemMessage(content=system_prompt)] + history

        try:
            for _ in range(5):  # max tool-call iterations
                response = await self.llm.ainvoke(messages)
                messages.append(response)
                history.append(response)

                if not response.tool_calls:
                    return response.content

                for tool_call in response.tool_calls:
                    tool = _TOOL_MAP.get(tool_call["name"])
                    if tool:
                        result = await tool.ainvoke(tool_call["args"])
                    else:
                        result = {"error": f"unknown tool: {tool_call['name']}"}
                    tool_msg = ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                    messages.append(tool_msg)
                    history.append(tool_msg)

            return "抱歉，處理請求時發生問題。" if detect_language(user_message) == "zh-TW" else "Sorry, could not process your request."
        except Exception as e:
            logger.error(f"[Agent] process error: {e}")
            return "抱歉，系統發生錯誤。" if detect_language(user_message) == "zh-TW" else "Sorry, system error."


# Singleton
langgraph_agent = LangGraphAgent()
