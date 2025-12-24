import asyncio
import os
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

async def reset_and_repair_properly():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    db_name = os.getenv("FIRESTORE_DATABASE", "(default)")
    client = firestore.AsyncClient(project=project_id, database=db_name)
    
    # 1. Clear current slots to start fresh
    print("--- Clearing existing daily slots ---")
    slots = await client.collection("daily_slots").get()
    for s in slots:
        await client.collection("daily_slots").document(s.id).delete()

    # 2. Re-run repair with the new "Group Together" logic
    print("--- Fetching all confirmed reservations ---")
    res_docs = await client.collection("reservations").where("status", "==", "confirmed").get()
    
    daily_occupancy = {}
    TABLE_CONFIG = {
        "2F-B1": 6, "2F-C1": 4, "2F-D1": 4, "2F-A1": 1, "2F-A2": 1, "2F-A3": 1, "2F-A4": 1,
        "3F-F1": 6, "3F-G1": 4, "3F-H1": 4, "3F-I1": 4, "3F-E1": 1, "3F-E2": 1, "3F-E3": 1, "3F-E4": 1
    }

    for doc in res_docs:
        data = doc.to_dict()
        res_id, date, pax, name, time = doc.id, data.get("date"), data.get("pax"), data.get("name", "Unknown"), data.get("time", "??:??")
        if not date: continue
        if date not in daily_occupancy: daily_occupancy[date] = {}

        remaining_pax = pax
        assigned_tables = []
        
        # Sort by Capacity DESC to keep groups together in big blocks
        sorted_tables = sorted(TABLE_CONFIG.items(), key=lambda x: x[1], reverse=True)
        
        for tid, cap in sorted_tables:
            if remaining_pax <= 0: break
            current_booked = daily_occupancy[date].get(tid, {}).get("booked_pax", 0)
            available = cap - current_booked
            if available > 0:
                take = min(remaining_pax, available)
                if tid not in daily_occupancy[date]: daily_occupancy[date][tid] = {"booked_pax": 0, "bookings": []}
                daily_occupancy[date][tid]["booked_pax"] += take
                daily_occupancy[date][tid]["bookings"].append({"res_id": res_id, "name": name, "pax": take, "time": time})
                remaining_pax -= take
                assigned_tables.append(tid)

        if assigned_tables:
            await client.collection("reservations").document(res_id).update({"table_id": assigned_tables[0]})
            print(f"Assigned {name} ({pax}p) on {date} to: {assigned_tables}")

    print("\n--- Saving Repaired Daily Slots ---")
    for date, occupancy in daily_occupancy.items():
        await client.collection("daily_slots").document(date).set({"occupancy": occupancy})

if __name__ == "__main__":
    asyncio.run(reset_and_repair_properly())
