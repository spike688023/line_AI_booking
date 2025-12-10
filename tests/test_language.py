import asyncio
import os
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

from src.agents import conversation_agent

async def test_language():
    print("Testing Language Adaptation...")
    
    # Test 1: English Input
    input_en = "What is on the menu?"
    print(f"\nInput (EN): {input_en}")
    response_en = await conversation_agent.process(input_en, context={"user_id": "test_user"})
    print(f"Response: {response_en}")
    
    # Test 2: Chinese Input
    input_zh = "請問菜單有什麼？"
    print(f"\nInput (ZH): {input_zh}")
    response_zh = await conversation_agent.process(input_zh, context={"user_id": "test_user"})
    print(f"Response: {response_zh}")

if __name__ == "__main__":
    asyncio.run(test_language())
