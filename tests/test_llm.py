import asyncio
import os
from dotenv import load_dotenv

# Load env vars first
load_dotenv()

from src.agents import conversation_agent

async def test_llm():
    print("Testing LLM Integration...")
    
    # Test case 1: Greeting (Should trigger LLM)
    input_text = "Hi, what can you do?"
    print(f"Input: {input_text}")
    response = await conversation_agent.process(input_text, context={"user_id": "test_user"})
    print(f"Response: {response}")
    
    # Test case 2: Menu query (Should trigger LLM)
    input_text = "What do you recommend?"
    print(f"Input: {input_text}")
    response = await conversation_agent.process(input_text, context={"user_id": "test_user"})
    print(f"Response: {response}")

    # Test case 3: Booking (Should trigger ReservationAgent)
    input_text = "Book 2023-10-27 18:00 4"
    print(f"Input: {input_text}")
    response = await conversation_agent.process(input_text, context={"user_id": "test_user"})
    print(f"Response: {response}")

if __name__ == "__main__":
    if not os.getenv("GOOGLE_API_KEY"):
        print("Error: GOOGLE_API_KEY not set in .env")
    else:
        asyncio.run(test_llm())
