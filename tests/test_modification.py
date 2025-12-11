import asyncio
import logging
from dotenv import load_dotenv
import os

# Load env vars first
load_dotenv()

from src.agents import conversation_agent
from src.database import db

# Configure logging
logging.basicConfig(level=logging.INFO)

async def test_modification():
    print("=== Testing Reservation Modification ===")
    
    user_id = "test_user_mod"
    
    # 1. Create a reservation first (Directly via DB to save time/tokens)
    print("\n[1] Creating initial reservation...")
    res_id = await db.create_reservation(
        user_id=user_id,
        date="2023-12-25",
        time="18:00",
        pax=4,
        name="Modification Tester",
        phone="0912345678"
    )
    print(f"Created reservation: {res_id}")
    
    # 2. Test "Get My Reservations"
    print("\n[2] Testing 'GetMyReservations' via LLM...")
    input_text = "Check my reservations"
    response = await conversation_agent.process(input_text, context={"user_id": user_id})
    print(f"LLM Response:\n{response}")
    
    # 3. Test "Modify Reservation"
    print("\n[3] Testing 'Modify Reservation' via LLM...")
    # We simulate the user asking to change the time
    # Note: In a real chat, the LLM would see the ID from the previous turn's context.
    # Here, we might need to be explicit or rely on the LLM finding the ID if we passed history (which we don't in this simple test).
    # So we will be explicit with the ID for this unit test to test the tool call.
    input_text = f"Change reservation {res_id} to 2023-12-26 at 19:00"
    response = await conversation_agent.process(input_text, context={"user_id": user_id})
    print(f"LLM Response:\n{response}")
    
    # 4. Verify in DB
    print("\n[4] Verifying in DB...")
    updated_res = await db.get_user_reservations(user_id)
    for r in updated_res:
        if r['id'] == res_id:
            print(f"Reservation in DB: Date={r['date']}, Time={r['time']}")
            assert r['date'] == "2023-12-26"
            assert r['time'] == "19:00"
            print("Verification Successful! ✅")

if __name__ == "__main__":
    asyncio.run(test_modification())
