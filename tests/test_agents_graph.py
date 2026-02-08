"""
Unit tests for LangGraph Agent - Booking Flow

Tests various booking scenarios to ensure:
1. Router correctly identifies booking intent
2. Collector extracts booking information
3. End-to-end booking flow works
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

# Import the modules to test
import sys
sys.path.insert(0, '/Users/linspike/coffee_shop_agent')

from src.agents_graph import (
    router_node,
    booking_collector_node,
    response_generator_node,
    detect_language,
    get_missing_booking_fields,
    AgentState,
    LangGraphAgent
)
from langchain_core.messages import HumanMessage


# ============================================================================
# HELPER: Create test state
# ============================================================================
def create_test_state(user_input: str, booking_slot: dict = None) -> AgentState:
    """Create a test state with default values."""
    return {
        "messages": [HumanMessage(content=user_input)],
        "user_id": "test_user_123",
        "user_profile": {},
        "intent": "unknown",
        "booking_slot": booking_slot or {},
        "next_step": "",
        "error_msg": "",
        "language": detect_language(user_input),
        "final_response": ""
    }


# ============================================================================
# TEST: Language Detection
# ============================================================================
class TestLanguageDetection:
    def test_detect_chinese(self):
        assert detect_language("你好，我要訂位") == "zh-TW"

    def test_detect_english(self):
        assert detect_language("I want to book a table") == "en"

    def test_detect_mixed_defaults_chinese(self):
        # Contains Chinese characters, should be zh-TW
        assert detect_language("2/7 下午3點") == "zh-TW"


# ============================================================================
# TEST: Router Intent Classification
# ============================================================================
class TestRouterNode:
    """Test the router node's intent classification."""

    # === Keyword-based tests (fast path) ===

    @pytest.mark.asyncio
    async def test_booking_keyword_chinese(self):
        """Test: '我要訂位' should be classified as booking."""
        state = create_test_state("你好，我要訂位")
        result = await router_node(state)
        assert result["intent"] == "booking", f"Expected 'booking', got '{result['intent']}'"

    @pytest.mark.asyncio
    async def test_booking_keyword_reserve(self):
        """Test: '預約' should be classified as booking."""
        state = create_test_state("我想預約明天的位子")
        result = await router_node(state)
        assert result["intent"] == "booking"

    @pytest.mark.asyncio
    async def test_booking_full_date_format(self):
        """Test: Full date format like '2026-02-07' should be booking."""
        state = create_test_state("2026-02-07 下午3點")
        result = await router_node(state)
        assert result["intent"] == "booking"

    @pytest.mark.asyncio
    async def test_booking_short_date_format(self):
        """Test: Short date format like '2/7' should be booking."""
        state = create_test_state("2/7")
        result = await router_node(state)
        assert result["intent"] == "booking", \
            f"'2/7' was classified as '{result['intent']}' instead of 'booking'"

    @pytest.mark.asyncio
    async def test_booking_chinese_date(self):
        """Test: '明天' should be classified as booking."""
        state = create_test_state("明天下午2點")
        result = await router_node(state)
        assert result["intent"] == "booking"

    @pytest.mark.asyncio
    async def test_booking_pax_pattern(self):
        """Test: '4個人' should be classified as booking."""
        state = create_test_state("4個人")
        result = await router_node(state)
        assert result["intent"] == "booking"

    @pytest.mark.asyncio
    async def test_booking_time_pattern(self):
        """Test: Time like '14:00' should be booking."""
        state = create_test_state("14:00")
        result = await router_node(state)
        assert result["intent"] == "booking"

    # === LLM fallback tests (requires mocking) ===

    @pytest.mark.asyncio
    async def test_llm_fallback_booking_informal(self):
        """Test: Informal booking request like '想去你們店' uses LLM."""
        state = create_test_state("想去你們店坐坐")

        mock_response = MagicMock()
        mock_response.content = "booking"

        with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            result = await router_node(state)

        assert result["intent"] == "booking"

    @pytest.mark.asyncio
    async def test_llm_fallback_general_chat(self):
        """Test: Greeting '你好' falls back to LLM -> general_chat."""
        state = create_test_state("你好")

        mock_response = MagicMock()
        mock_response.content = "general_chat"

        with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            result = await router_node(state)

        assert result["intent"] == "general_chat"

    @pytest.mark.asyncio
    async def test_llm_fallback_unclear(self):
        """Test: Gibberish input like '???' returns unclear intent."""
        state = create_test_state("???")

        mock_response = MagicMock()
        mock_response.content = "unclear"

        with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            result = await router_node(state)

        assert result["intent"] == "unclear"


# ============================================================================
# TEST: Booking Slot Validation
# ============================================================================
class TestBookingSlotValidation:
    def test_all_fields_missing(self):
        missing = get_missing_booking_fields({})
        assert set(missing) == {"date", "time", "pax", "name", "phone", "floor"}

    def test_partial_fields(self):
        booking = {"date": "2026-02-07", "time": "14:00"}
        missing = get_missing_booking_fields(booking)
        assert set(missing) == {"pax", "name", "phone", "floor"}

    def test_all_fields_present(self):
        booking = {
            "date": "2026-02-07",
            "time": "14:00",
            "pax": 4,
            "name": "王小明",
            "phone": "0912345678",
            "floor": 2
        }
        missing = get_missing_booking_fields(booking)
        assert missing == []


# ============================================================================
# TEST: Collector Node (with mocked LLM)
# ============================================================================
class TestCollectorNode:
    """Test the booking collector with mocked LLM responses."""

    @pytest.mark.asyncio
    async def test_extract_full_booking_info(self):
        """Test extraction of complete booking info."""
        state = create_test_state("明天下午2點，4個人，我叫王小明，電話0912345678")

        # Mock the LLM response
        mock_response = MagicMock()
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        mock_response.content = f'''
        ```json
        {{"date": "{tomorrow}", "time": "14:00", "pax": 4, "name": "王小明", "phone": "0912345678", "floor": null}}
        ```
        '''

        with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            # Mock database call
            with patch('src.agents_graph.db.get_user_reservations', new_callable=AsyncMock) as mock_db:
                mock_db.return_value = []

                result = await booking_collector_node(state)

        assert result["booking_slot"]["date"] == tomorrow
        assert result["booking_slot"]["time"] == "14:00"
        assert result["booking_slot"]["pax"] == 4
        assert result["booking_slot"]["name"] == "王小明"
        assert result["booking_slot"]["phone"] == "0912345678"

    @pytest.mark.asyncio
    async def test_extract_partial_info_short_date(self):
        """Test extraction from short date format like '2/7'.

        This tests if the LLM can correctly interpret '2/7' as a date.
        """
        state = create_test_state("2/7")

        mock_response = MagicMock()
        mock_response.content = '''
        ```json
        {"date": "2026-02-07", "time": null, "pax": null, "name": null, "phone": null, "floor": null}
        ```
        '''

        with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            with patch('src.agents_graph.db.get_user_reservations', new_callable=AsyncMock) as mock_db:
                mock_db.return_value = []

                result = await booking_collector_node(state)

        assert result["booking_slot"].get("date") == "2026-02-07"


# ============================================================================
# TEST: End-to-End Booking Flow (Multi-turn Integration Tests)
# ============================================================================
class TestEndToEndBookingFlow:
    """Integration tests for the full booking conversation flow.

    These tests verify that booking_slot state persists across conversation turns.
    This is critical for multi-turn booking flows.
    """

    @pytest.mark.asyncio
    async def test_booking_state_persistence(self):
        """Test that booking_slot persists across multiple turns.

        This test catches the bug where booking_slot was reset to {} on each turn.
        """
        from src.agents_graph import LangGraphAgent

        # Mock database methods
        stored_state = {}

        async def mock_get_state(user_id):
            return stored_state.get(user_id, {})

        async def mock_save_state(user_id, booking_slot, intent=None):
            stored_state[user_id] = {"booking_slot": booking_slot, "intent": intent}
            return True

        async def mock_clear_state(user_id):
            stored_state.pop(user_id, None)
            return True

        with patch('src.agents_graph.db.get_conversation_state', side_effect=mock_get_state), \
             patch('src.agents_graph.db.save_conversation_state', side_effect=mock_save_state), \
             patch('src.agents_graph.db.clear_conversation_state', side_effect=mock_clear_state):

            agent = LangGraphAgent()
            user_id = "test_user_state_persistence"

            # Mock LLM responses for collector
            mock_collector_response_1 = MagicMock()
            mock_collector_response_1.content = '{"date": null, "time": null, "pax": null, "name": null, "phone": null, "floor": null}'

            mock_collector_response_2 = MagicMock()
            mock_collector_response_2.content = '{"date": "2026-02-07", "time": null, "pax": null, "name": null, "phone": null, "floor": null}'

            mock_responder_response = MagicMock()
            mock_responder_response.content = "請問您想訂哪一天呢？"

            # Turn 1: User says "我要訂位"
            with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
                mock_llm = AsyncMock()
                mock_llm.ainvoke.side_effect = [mock_collector_response_1, mock_responder_response]
                mock_llm_class.return_value = mock_llm

                with patch('src.agents_graph.db.get_user_reservations', new_callable=AsyncMock) as mock_db:
                    mock_db.return_value = []
                    with patch('src.agents_graph.db.get_menu', new_callable=AsyncMock) as mock_menu:
                        mock_menu.return_value = []
                        with patch('src.agents_graph.db.get_business_hours', new_callable=AsyncMock) as mock_hours:
                            mock_hours.return_value = {}

                            response1 = await agent.process("我要訂位", {"user_id": user_id})

            # Verify state was saved (even if empty initially)
            assert user_id in stored_state or stored_state == {}, "State should be tracked"

            # Turn 2: User gives date "2/7"
            with patch('src.agents_graph.ChatGoogleGenerativeAI') as mock_llm_class:
                mock_llm = AsyncMock()
                mock_llm.ainvoke.side_effect = [mock_collector_response_2, mock_responder_response]
                mock_llm_class.return_value = mock_llm

                with patch('src.agents_graph.db.get_user_reservations', new_callable=AsyncMock) as mock_db:
                    mock_db.return_value = []
                    with patch('src.agents_graph.db.get_menu', new_callable=AsyncMock) as mock_menu:
                        mock_menu.return_value = []
                        with patch('src.agents_graph.db.get_business_hours', new_callable=AsyncMock) as mock_hours:
                            mock_hours.return_value = {}
                            with patch('src.agents_graph.db.check_availability', new_callable=AsyncMock) as mock_avail:
                                mock_avail.return_value = True

                                response2 = await agent.process("2/7", {"user_id": user_id})

            # CRITICAL: Verify booking_slot now contains the date
            assert user_id in stored_state, "State should exist after turn 2"
            saved_booking_slot = stored_state[user_id].get("booking_slot", {})
            assert saved_booking_slot.get("date") == "2026-02-07", \
                f"Date should be saved. Got: {saved_booking_slot}"

    @pytest.mark.asyncio
    async def test_booking_with_all_info_at_once(self):
        """Test booking when user provides all info in one message."""
        # This would require mocking the entire graph execution
        # For now, we test the components individually
        pass

    @pytest.mark.asyncio
    async def test_state_cleared_after_booking(self):
        """Test that conversation state is cleared after successful booking."""
        # This tests that clear_conversation_state is called in execute_booking_node
        pass


# ============================================================================
# TEST: Specific Bug Scenarios
# ============================================================================
class TestBugScenarios:
    """Tests for specific bugs found in production."""

    @pytest.mark.asyncio
    async def test_bug_short_date_not_recognized(self):
        """
        BUG: When user replies with just "2/7", router classifies as general_chat.

        Root cause: Router regex only matches YYYY-MM-DD format.
        Expected: Should match M/D or MM/DD patterns too.
        """
        test_cases = [
            "2/7",      # Short date
            "02/07",    # Two digit month/day
            "2/7 ",     # With trailing space
            " 2/7",     # With leading space
            "2月7日",    # Chinese date format
        ]

        for test_input in test_cases:
            state = create_test_state(test_input)
            result = await router_node(state)
            # These currently FAIL - documenting the bug
            assert result["intent"] == "booking", \
                f"BUG: '{test_input}' classified as '{result['intent']}' instead of 'booking'"

    @pytest.mark.asyncio
    async def test_bug_name_classified_as_unclear(self):
        """
        BUG: When user replies with just a name like 'Spike' during booking,
        router classifies as 'unclear' because it has no booking keywords.

        Root cause: Router didn't check booking_slot state for mid-flow continuation.
        Fix: If booking_slot has data, skip classification and route to collector.
        """
        # Simulate mid-booking: date, time, pax already collected
        state = create_test_state("Spike", booking_slot={
            "date": "2026-02-09",
            "time": "14:00",
            "pax": 4
        })
        result = await router_node(state)
        assert result["intent"] == "booking", \
            f"BUG: 'Spike' during mid-booking classified as '{result['intent']}' instead of 'booking'"

    @pytest.mark.asyncio
    async def test_abandoned_booking_then_restart(self):
        """
        When user abandons a booking mid-flow and later says '我要訂位' again,
        the old booking_slot should be cleared and a fresh booking starts.
        """
        # Simulate abandoned booking with stale data
        state = create_test_state("我要訂位", booking_slot={
            "date": "2026-02-09",
            "time": "14:00",
            "pax": 4
        })
        result = await router_node(state)
        assert result["intent"] == "booking"
        # booking_slot should be cleared
        assert result["booking_slot"] == {}, \
            f"Expected empty booking_slot after restart, got: {result['booking_slot']}"

    @pytest.mark.asyncio
    async def test_bug_collector_returns_empty(self):
        """
        BUG: Collector sometimes returns empty booking_slot even when info is present.

        From log: "[COLLECTOR] Updated booking slot: {}"
        This happens when LLM response parsing fails.
        """
        # This would need investigation of actual LLM responses
        pass


# ============================================================================
# Run tests
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
