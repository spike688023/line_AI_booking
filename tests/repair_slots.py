import asyncio
import os
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

async def sync_reservations_to_slots():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    db_name = os.getenv("FIRESTORE_DATABASE", "(default)")
    client = firestore.AsyncClient(project=project_id, database=db_name)
    
    print("--- Fetching all confirmed reservations ---")
    res_docs = await client.collection("reservations").where("status", "==", "confirmed").get()
    
    # Track slots by date
    daily_occupancy = {} # {date: {table_id: {booked_pax, bookings}}}
    
    TABLE_CONFIG = {
        "2F-B1": 6, "2F-A1": 1, "2F-A2": 1, "2F-A3": 1, "2F-A4": 1, "2F-C1": 4, "2F-D1": 4,
        "3F-F1": 6, "3F-E1": 1, "3F-E2": 1, "3F-E3": 1, "3F-E4": 1, "3F-G1": 4, "3F-H1": 4, "3F-I1": 4
    }

    for doc in res_docs:
        data = doc.to_dict()
        res_id = doc.id
        date = data.get("date")
        pax = data.get("pax")
        name = data.get("name", "Unknown")
        time = data.get("time", "??:??")
        table_id = data.get("table_id")
        
        if not date: continue
        
        if date not in daily_occupancy:
            daily_occupancy[date] = {}
            
        # If the reservation has a table assigned, update the occupancy
        if table_id and table_id in TABLE_CONFIG:
            if table_id not in daily_occupancy[date]:
                daily_occupancy[date][table_id] = {"booked_pax": 0, "bookings": []}
            daily_occupancy[date][table_id]["booked_pax"] += pax
            daily_occupancy[date][table_id]["bookings"].append({
                "res_id": res_id, "name": name, "pax": pax, "time": time
            })
        else:
            print(f"Repairing Res {res_id}: {name} ({pax}p) on {date} - No table assigned.")
            # Simple allocation logic for repair
            remaining_pax = pax
            assigned_tables = []
            
            # Find tables with capacity, prioritizing larger tables for the remainder
            # Sort tables by capacity descending to keep groups together
            sorted_tables = sorted(TABLE_CONFIG.items(), key=lambda x: x[1], reverse=True)
            
            for tid, cap in sorted_tables:
                if remaining_pax <= 0: break
                
                current_booked = daily_occupancy[date].get(tid, {}).get("booked_pax", 0)
                available = cap - current_booked
                
                if available > 0:
                    # Take as many as possible (either all remaining or full table)
                    take = min(remaining_pax, available)
                    if tid not in daily_occupancy[date]:
                        daily_occupancy[date][tid] = {"booked_pax": 0, "bookings": []}
                    
                    daily_occupancy[date][tid]["booked_pax"] += take
                    daily_occupancy[date][tid]["bookings"].append({
                        "res_id": res_id, "name": name, "pax": take, "time": time
                    })
                    remaining_pax -= take
                    assigned_tables.append(tid)
            
            # Update the reservation with the first table ID (simplification)
            if assigned_tables:
                await client.collection("reservations").document(res_id).update({"table_id": assigned_tables[0]})
                print(f"  -> Assigned to {assigned_tables}")
            else:
                print(f"  !! ERROR: Could not find space for reservation {res_id}")

    # Write all slots to Firestore
    print("\n--- Saving Daily Slots ---")
    for date, occupancy in daily_occupancy.items():
        print(f"Updating slot for {date}...")
        await client.collection("daily_slots").document(date).set({"occupancy": occupancy}, merge=True)

if __name__ == "__main__":
    asyncio.run(sync_reservations_to_slots())
