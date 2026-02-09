"""
Test script for LangGraph Agent

Usage:
    python test_langgraph.py
"""

import asyncio
import logging
from src.agents_graph import langgraph_agent

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

async def test_booking_flow():
    """Test a complete booking conversation flow."""
    
    print("\n" + "="*60)
    print("Testing LangGraph Agent - Booking Flow")
    print("="*60 + "\n")
    
    test_cases = [
        {
            "input": "我想訂位",
            "context": {"user_id": "test_user_001"},
            "description": "Test 1: Initial booking intent (Chinese)"
        },
        {
            "input": "明天下午2點，4個人",
            "context": {"user_id": "test_user_001"},
            "description": "Test 2: Provide date, time, pax"
        },
        {
            "input": "I want to book a table",
            "context": {"user_id": "test_user_002"},
            "description": "Test 3: Initial booking intent (English)"
        },
        {
            "input": "2026-02-05 at 14:00 for 2 people",
            "context": {"user_id": "test_user_002"},
            "description": "Test 4: Full booking info in one message (English)"
        },
        {
            "input": "我要訂2026-02-10 18:00，6個人，我叫陳小明，電話0912345678",
            "context": {"user_id": "test_user_003"},
            "description": "Test 5: Complete booking info (Chinese)"
        }
    ]
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n{'─'*60}")
        print(f"📝 {test['description']}")
        print(f"{'─'*60}")
        print(f"👤 User: {test['input']}")
        
        try:
            response = await langgraph_agent.process(
                test['input'],
                context=test['context']
            )
            print(f"🤖 Agent: {response}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    print("\n" + "="*60)
    print("Testing Complete")
    print("="*60 + "\n")


async def test_language_consistency():
    """Test that agent maintains language consistency."""
    
    print("\n" + "="*60)
    print("Testing Language Consistency")
    print("="*60 + "\n")
    
    # Test Chinese input
    print("Test: Chinese input should get Chinese response")
    response = await langgraph_agent.process(
        "你好，請問有什麼可以幫忙的嗎？",
        context={"user_id": "lang_test_zh"}
    )
    print(f"Response: {response}")
    
    # Check if response is in Chinese
    try:
        response.encode('ascii')
        print("⚠️  WARNING: Response appears to be in English!")
    except UnicodeEncodeError:
        print("✅ Response is in Chinese")
    
    await asyncio.sleep(1)
    
    # Test English input
    print("\nTest: English input should get English response")
    response = await langgraph_agent.process(
        "Hello, what can you help me with?",
        context={"user_id": "lang_test_en"}
    )
    print(f"Response: {response}")
    
    # Check if response is in English
    try:
        response.encode('ascii')
        print("✅ Response is in English")
    except UnicodeEncodeError:
        print("⚠️  WARNING: Response appears to be in Chinese!")


async def test_business_rules():
    """Test business rule validation."""
    
    print("\n" + "="*60)
    print("Testing Business Rules Validation")
    print("="*60 + "\n")
    
    # Test past date rejection
    print("Test: Past date should be rejected")
    response = await langgraph_agent.process(
        "我要訂2025-01-01 12:00，2個人，叫王小明，電話0987654321",
        context={"user_id": "rules_test_001"}
    )
    print(f"Response: {response}")
    
    if "過去" in response or "past" in response.lower():
        print("✅ Correctly rejected past date")
    else:
        print("⚠️  WARNING: Did not detect past date issue")


async def main():
    """Run all tests."""
    
    print("\n🚀 Starting LangGraph Agent Tests\n")
    
    # Run test suites
    await test_booking_flow()
    await test_language_consistency()
    await test_business_rules()
    
    print("\n✨ All tests completed!\n")


if __name__ == "__main__":
    asyncio.run(main())
