
import asyncio
import os
import logging
from dotenv import load_dotenv
from google.cloud import firestore

# Load environment variables
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def repair_daily_slots():
    """
    Rebuilds 'daily_slots' collection based on existing 'reservations'.
    This will fix synchronization issues between reservations and the seating map.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    db_name = os.getenv("FIRESTORE_DATABASE")
    
    if not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT not set.")
        return

    # Initialize Firestore
    if db_name:
        db = firestore.AsyncClient(project=project_id, database=db_name)
    else:
        db = firestore.AsyncClient(project=project_id)

    logger.info("Starting Daily Slots Repair...")

    # 1. Clear existing daily_slots for future dates (optional but safer to rebuild)
    # Ideally, we iterate through all reservations and build a fresh map.
    
    # Map: Date -> { TableID: { booked_pax: 0, bookings: [] } }
    temp_slots = {}
    
    # 2. Fetch ALL future reservations
    reservations_ref = db.collection("reservations")
    # We can fetch all, or just future. Rebuilding all is safer to ensure consistency.
    docs = reservations_ref.stream()
    
    count = 0
    async for doc in docs:
        res = doc.to_dict()
        res_id = doc.id
        date = res.get("date")
        time = res.get("time")
        pax = res.get("pax", 0)
        name = res.get("name", "Unknown")
        phone = res.get("phone", "")
        # table_id might be "2F-A1" or "2F-A1, 2F-A2"
        all_tables_str = res.get("all_tables") or res.get("table_id") 
        
        if not date or not all_tables_str:
            continue
            
        tables = [t.strip() for t in all_tables_str.split(",")]
        
        if date not in temp_slots:
            temp_slots[date] = {}
            
        # Distribute pax across tables (Simplified logic: we don't know exact split if not stored)
        # But we know total pax.
        # In create_reservation, we stored occupancy per table.
        # Here we have to infer or just put the whole booking on the first table?
        # WAIT! create_reservation stored precise split in occupancy.
        # If we rebuild, we lose that precise split info if it wasn't stored in reservation doc.
        # CHECK: reservation doc has 'pax' (total) and 'all_tables'. It DOES NOT store per-table pax.
        # This is a limitation. create_reservation calculates it and writes to daily_slots, but doesn't save the split details to reservation doc.
        
        # Heuristic for Repair:
        # If multiple tables, we assume roughly even split or purely capacity based logic?
        # Actually, for just "clearing" the phantom ghosts, we only need to account for existing reservations.
        # If we wipe daily_slots first, then re-add existing reservations.
        
        # Let's try to do a best-effort reconstruction.
        # We need Table Capacity to do this right.
        # Hardcoding capacities from src/database.py
        TABLE_CAPACITIES = {
             "2F-B1": 6, "2F-A1": 1, "2F-A2": 1, "2F-A3": 1, "2F-A4": 1, "2F-C1": 4, "2F-D1": 4,
             "3F-F1": 6, "3F-E1": 1, "3F-E2": 1, "3F-E3": 1, "3F-E4": 1, "3F-G1": 4, "3F-H1": 4, "3F-I1": 4
        }
        
        remaining_pax = pax
        
        for i, tid in enumerate(tables):
            if tid not in temp_slots[date]:
                temp_slots[date][tid] = {"booked_pax": 0, "bookings": []}
                
            # Estimate pax for this table
            cap = TABLE_CAPACITIES.get(tid, 4)
            if i == len(tables) - 1:
                take = remaining_pax # Last table takes the rest
            else:
                take = min(cap, remaining_pax)
            
            remaining_pax -= take
            if take <= 0: take = 0 # Should not happen if logic was sound
            
            temp_slots[date][tid]["booked_pax"] += take
            temp_slots[date][tid]["bookings"].append({
                "res_id": res_id,
                "name": name,
                "pax": take,
                "time": time,
                "phone_suffix": phone[-4:] if phone else "????"
            })
            
        count += 1

    logger.info(f"Processed {count} valid reservations.")
    
    # 3. Write back to Firestore
    # WARNING: This overwrites entire daily_slots for the dates found.
    # Dates with NO reservations should strictly be empty or deleted.
    
    # First, let's list all daily_slots docs to find "Ghost" dates (dates with data but no reservations)
    ds_ref = db.collection("daily_slots")
    all_dates_snapshots = ds_ref.stream()
    
    updated_count = 0
    deleted_count = 0
    
    async for doc in all_dates_snapshots:
        date_key = doc.id
        # We process current date and future only to be safe? 
        # Or just everything? Everything is cleaner.
        
        if date_key in temp_slots:
            # Overwrite with reconstructed data
            await ds_ref.document(date_key).set({"occupancy": temp_slots[date_key]})
            updated_count += 1
            # Remove from temp so we know we handled it
            del temp_slots[date_key]
        else:
            # This date exists in DB but has NO reservations in our reconstruction
            # DELETE IT (It's a GHOST!)
            await ds_ref.document(date_key).delete()
            deleted_count += 1
            logger.info(f"Deleted ghost slot: {date_key}")

    # Process remaining new dates (dates that weren't in daily_slots before but now have reservations? Unlikely but possible)
    for date_key, occupancy in temp_slots.items():
        await ds_ref.document(date_key).set({"occupancy": occupancy})
        updated_count += 1

    logger.info(f"Repair Complete. Updated {updated_count} days. Deleted {deleted_count} ghost days.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(repair_daily_slots())
