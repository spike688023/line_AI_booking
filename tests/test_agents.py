import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.agents import ReservationQueryAgent

@pytest.fixture
def mock_db():
    with patch("src.agents.db") as mock:
        yield mock

@pytest.fixture
def agent(mock_db):
    # Mock GenerativeModel to prevent real API calls
    with patch("src.agents.genai.GenerativeModel") as MockModel:
        # Pass the mock_db explicitly to the agent
        agent = ReservationQueryAgent(database=mock_db)
        agent.chat = MagicMock() # Mock the chat session
        agent.chat.send_message_async = AsyncMock()
        return agent

@pytest.mark.asyncio
async def test_process_booking_intent(agent, mock_db):
    # Simulate LLM response: Function Call to book_table
    mock_response = MagicMock()
    mock_part = MagicMock()
    
    # Configure function call
    mock_part.function_call.name = "book_table"
    mock_part.function_call.args = {
        "date": "2025-12-25",
        "time": "18:00",
        "pax": 2,
        "name": "Test User",
        "phone": "0912345678"
    }
    
    mock_response.parts = [mock_part]
    mock_response.text = None # Function call usually has no text
    
    # Mock chat.send_message_async to return this response
    agent.chat.send_message_async.return_value = mock_response
    
    # Mock DB result
    mock_db.check_availability = AsyncMock(return_value=True)
    mock_db.create_reservation = AsyncMock(return_value="res_123")
    
    # Execute
    response_text = await agent.process("I want to book a table", context={"user_id": "user123"})
    
    # Verify
    mock_db.create_reservation.assert_called_once()
    assert "res_123" in response_text or "成功" in response_text or "Success" in response_text

@pytest.mark.asyncio
async def test_process_query_intent(agent, mock_db):
    # Simulate LLM response: Function Call to get_my_reservations
    mock_response = MagicMock()
    mock_part = MagicMock()
    
    mock_part.function_call.name = "get_my_reservations"
    mock_part.function_call.args = {"include_past": False}
    
    mock_response.parts = [mock_part]
    agent.chat.send_message_async.return_value = mock_response
    
    # Mock DB result
    mock_db.get_user_reservations = AsyncMock(return_value=[
        {"id": "res1", "date": "2025-12-25", "time": "18:00", "pax": 2, "name": "Test", "phone": "09123"}
    ])
    
    # Execute
    response_text = await agent.process("Check my bookings", context={"user_id": "user123"})
    
    # Verify
    mock_db.get_user_reservations.assert_called_once()
    assert "2025-12-25" in response_text

@pytest.mark.asyncio
async def test_process_modification(agent, mock_db):
    # Simulate LLM response: Function Call to modify_reservation
    mock_response = MagicMock()
    mock_part = MagicMock()
    
    mock_part.function_call.name = "modify_reservation"
    mock_part.function_call.args = {
        "reservation_id": "res1",
        "new_date": "2025-12-26",
        "new_time": "19:00"
    }
    
    mock_response.parts = [mock_part]
    agent.chat.send_message_async.return_value = mock_response
    
    # Mock DB result
    mock_db.modify_reservation = AsyncMock(return_value="success")
    
    # Execute
    response_text = await agent.process("Change my booking", context={"user_id": "user123"})
    
    # Verify
    mock_db.modify_reservation.assert_called_once()
    assert "success" in response_text.lower() or "成功" in response_text
