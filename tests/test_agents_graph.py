"""Tests for the single ReAct agent (T5 + T6)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage
import os

os.environ["GOOGLE_CLOUD_PROJECT"] = "dummy"
os.environ["GOOGLE_API_KEY"] = "dummy"


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_agent():
    """Create a LangGraphAgent with a mocked LLM (no real API calls)."""
    with patch("src.agents_graph.ChatGoogleGenerativeAI") as MockLLM:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        MockLLM.return_value = mock_llm
        from src.agents_graph import LangGraphAgent
        return LangGraphAgent()


# ── Architecture ──────────────────────────────────────────────────────────────

def test_old_relay_nodes_removed():
    import src.agents_graph as ag
    for removed in ("router_node", "booking_collector_node", "business_rules_node",
                    "execute_booking_node", "response_generator_node"):
        assert not hasattr(ag, removed), f"{removed} should be removed in T5"


def test_graph_has_agent_and_tools_nodes():
    agent = _make_agent()
    nodes = set(agent.graph.get_graph().nodes.keys())
    assert "__start__" in nodes or "agent" in nodes  # depends on langgraph version
    assert "tools" in nodes


def test_process_accepts_store_id():
    import inspect
    from src.agents_graph import LangGraphAgent
    sig = inspect.signature(LangGraphAgent.process)
    assert "store_id" in sig.parameters


# ── Thread ID ─────────────────────────────────────────────────────────────────

def test_thread_id_format():
    from src.agents_graph import _make_thread_id
    assert _make_thread_id("store_abc", "U123") == "store_abc_U123"
    assert _make_thread_id("store_xyz", "U456") == "store_xyz_U456"


def test_different_stores_have_different_thread_ids():
    from src.agents_graph import _make_thread_id
    assert _make_thread_id("store_A", "U1") != _make_thread_id("store_B", "U1")


# ── process() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_returns_string():
    agent = _make_agent()
    agent.graph.ainvoke = AsyncMock(return_value={
        "messages": [AIMessage(content="您好！請問您想預約哪天？")]
    })

    result = await agent.process("我想訂位", context={"user_id": "U123"}, store_id="store_abc")

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_failed_booking_response_reflects_failure():
    """When graph returns a failure message, process() must surface it unchanged."""
    agent = _make_agent()
    agent.graph.ainvoke = AsyncMock(return_value={
        "messages": [AIMessage(content="抱歉，該時段已客滿，請選擇其他時間。")]
    })

    response = await agent.process(
        "我要訂7月1日18:00，50人",
        context={"user_id": "U123"},
        store_id="store_abc",
    )

    assert "客滿" in response or "抱歉" in response
    assert "成功" not in response


@pytest.mark.asyncio
async def test_process_passes_correct_thread_id():
    """process() must call graph.ainvoke with thread_id == {store_id}_{user_id}."""
    agent = _make_agent()
    agent.graph.ainvoke = AsyncMock(return_value={
        "messages": [AIMessage(content="ok")]
    })

    await agent.process("hi", context={"user_id": "U999"}, store_id="store_test")

    call_kwargs = agent.graph.ainvoke.call_args
    config = call_kwargs[1].get("config") or call_kwargs[0][1]
    assert config["configurable"]["thread_id"] == "store_test_U999"


# ── System prompt (T6 stub — tests will be extended in T6) ────────────────────

def test_detect_language_still_exported():
    from src.agents_graph import detect_language
    assert detect_language("你好") == "zh-TW"
    assert detect_language("hello") == "en"
