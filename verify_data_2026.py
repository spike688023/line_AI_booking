import asyncio
import os
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

async def verify_seating_data(date_str):
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    db_name = os.getenv("FIRESTORE_DATABASE", "(default)")
    client = firestore.AsyncClient(project=project_id, database=db_name)
    
    print(f"--- Checking Reservations for {date_str} ---")
    res_ref = client.collection("reservations").where("date", "==", date_str)
    docs = await res_ref.get()
    for doc in docs:
        print(f"Reservation ID: {doc.id}")
        print(f"Data: {doc.to_dict()}")
        
    print(f"\n--- Checking Daily Slots for {date_str} ---")
    slot_ref = client.collection("daily_slots").document(date_str)
    slot_doc = await slot_ref.get()
    if slot_doc.exists:
        print(f"Daily Slot Data: {slot_doc.to_dict()}")
    else:
        print("Daily Slot document does not exist for this date.")

if __name__ == "__main__":
    asyncio.run(verify_seating_data("2026-02-20"))
