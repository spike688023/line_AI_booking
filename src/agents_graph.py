"""
LangGraph ReAct agent for the Coffee Shop booking system.

Single agent_node ↔ tools_node loop (T5).
Dynamic system prompt per store (T6).
"""

import logging
import os
from typing import Dict, Any, Literal

from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_google_genai import ChatGoogleGenerativeAI

from src.tools import check_availability, execute_booking

load_dotenv()
logger = logging.getLogger(__name__)

_TOOLS = [check_availability, execute_booking]

_STATIC_SYSTEM_PROMPT = """\
你是「言文字」咖啡廳的 AI 訂位助理。

【你的工作】
- 幫助用戶完成訂位（使用 check_availability 確認空位，再用 execute_booking 建立訂位）
- 回答咖啡廳相關問題

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
            prompt=_STATIC_SYSTEM_PROMPT,
            checkpointer=MemorySaver(),
        )
        logger.info("[LangGraphAgent] ReAct agent initialized")

    async def process(self, user_message: str, context: Dict[str, Any] = None, store_id: str = "") -> str:
        user_id = (context or {}).get("user_id", "unknown")
        thread_id = _make_thread_id(store_id, user_id)
        config = {"configurable": {"thread_id": thread_id}}

        try:
            result = await self.graph.ainvoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config=config,
            )
            return result["messages"][-1].content
        except Exception as e:
            logger.error(f"[LangGraphAgent] process error: {e}")
            return "抱歉，系統發生錯誤。" if detect_language(user_message) == "zh-TW" else "Sorry, system error."


# Singleton
langgraph_agent = LangGraphAgent()
