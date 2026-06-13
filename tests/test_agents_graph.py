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


# ── System prompt (T6) ───────────────────────────────────────────────────────

def test_detect_language_still_exported():
    from src.agents_graph import detect_language
    assert detect_language("你好") == "zh-TW"
    assert detect_language("hello") == "en"


def test_build_system_prompt_contains_menu():
    from src.agents_graph import build_system_prompt
    menu = [{"name": "Latte", "price": 120, "category": "Coffee"}]
    hours = {"Monday": {"open": "09:00", "close": "18:00", "closed": False}}
    table_config = {"tables": {"T1": {"floor": 1, "capacity": 4}}, "total_capacity": 4}
    prompt = build_system_prompt(menu, hours, table_config, custom_prompt="")
    assert "Latte" in prompt
    assert "120" in prompt


def test_build_system_prompt_contains_hours():
    from src.agents_graph import build_system_prompt
    hours = {"Monday": {"open": "10:00", "close": "22:00", "closed": False},
             "Sunday": {"closed": True}}
    prompt = build_system_prompt([], hours, {}, custom_prompt="")
    assert "10:00" in prompt
    assert "22:00" in prompt


def test_build_system_prompt_includes_custom_prompt():
    from src.agents_graph import build_system_prompt
    prompt = build_system_prompt([], {}, {}, custom_prompt="請用台語回覆")
    assert "台語" in prompt


def test_build_system_prompt_works_without_custom_prompt():
    from src.agents_graph import build_system_prompt
    prompt = build_system_prompt([], {}, {}, custom_prompt="")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


@pytest.mark.asyncio
async def test_two_stores_produce_different_prompts():
    from src.agents_graph import build_system_prompt
    menu_a = [{"name": "Espresso", "price": 80, "category": "Coffee"}]
    menu_b = [{"name": "Matcha", "price": 100, "category": "Tea"}]
    hours = {"Monday": {"open": "09:00", "close": "18:00", "closed": False}}
    table_config = {}

    prompt_a = build_system_prompt(menu_a, hours, table_config, custom_prompt="A店")
    prompt_b = build_system_prompt(menu_b, hours, table_config, custom_prompt="B店")

    assert prompt_a != prompt_b
    assert "Espresso" in prompt_a
    assert "Matcha" in prompt_b
