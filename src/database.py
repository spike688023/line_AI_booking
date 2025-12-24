import logging
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from google.cloud import firestore
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        try:
            db_name = os.getenv("FIRESTORE_DATABASE")
            if self.project_id:
                if db_name:
                    self.client = firestore.Client(project=self.project_id, database=db_name)
                    logger.info(f"Firestore client initialized for project: {self.project_id}, database: {db_name}")
                else:
                    self.client = firestore.Client(project=self.project_id)
                    logger.info(f"Firestore client initialized for project: {self.project_id} (default database)")
            else:
                logger.warning("GOOGLE_CLOUD_PROJECT not set. Firestore client not initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}")

    TOTAL_CAPACITY = 40  # Updated total capacity based on new layout

    # Table Configuration based on IMG_6126 (2F) and IMG_6127 (3F)
    TABLE_CONFIG = {
        # --- 2nd Floor ---
        "2F-B1": {"capacity": 6, "floor": 2},
        "2F-A1": {"capacity": 1, "floor": 2},
        "2F-A2": {"capacity": 1, "floor": 2},
        "2F-A3": {"capacity": 1, "floor": 2},
        "2F-A4": {"capacity": 1, "floor": 2},
        "2F-C1": {"capacity": 4, "floor": 2},
        "2F-D1": {"capacity": 4, "floor": 2},
        
        # --- 3rd Floor ---
        "3F-F1": {"capacity": 6, "floor": 3},
        "3F-E1": {"capacity": 1, "floor": 3},
        "3F-E2": {"capacity": 1, "floor": 3},
        "3F-E3": {"capacity": 1, "floor": 3},
        "3F-E4": {"capacity": 1, "floor": 3},
        "3F-G1": {"capacity": 4, "floor": 3},
        "3F-H1": {"capacity": 4, "floor": 3},
        "3F-I1": {"capacity": 4, "floor": 3},
    }

    async def get_daily_occupied_tables(self, date: str) -> Dict[str, Any]:
        """
        Get detailed occupancy for each table on a given date.
        Returns: { "table_id": {"booked_pax": X, "bookings": [{"name": "...", "pax": Y}, ...]} }
        """
        if not self.client:
            return {}

        try:
            slot_ref = self.client.collection("daily_slots").document(date)
            snapshot = slot_ref.get()
            
            if snapshot.exists:
                return snapshot.to_dict().get("occupancy", {})
            return {}
        except Exception as e:
            logger.error(f"Error fetching daily table occupancy: {e}")
            return {}

    async def check_availability(self, date: str, time: str, pax: int) -> bool:
        """
        Check if any table has enough REMAINING capacity for the given pax.
        """
        if not self.client:
            return True

        try:
            occupancy = await self.get_daily_occupied_tables(date)
            
            for table_id, config in self.TABLE_CONFIG.items():
                table_data = occupancy.get(table_id, {"booked_pax": 0})
                remaining = config["capacity"] - table_data["booked_pax"]
                if remaining >= pax:
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return False

    async def create_reservation(self, user_id: str, date: str, time: str, pax: int, name: str, phone: str) -> str:
        """
        Create a reservation and allocate seats on a table (supports shared tables).
        """
        if not self.client:
            return "mock-reservation-id"

        transaction = self.client.transaction()
        reservation_ref = self.client.collection("reservations").document()
        slot_ref = self.client.collection("daily_slots").document(date)

        @firestore.transactional
        def create_in_transaction(transaction, reservation_ref, slot_ref, date, time, pax, user_id, name, phone):
            # 1. Get current occupancy for the day
            snapshot = slot_ref.get(transaction=transaction)
            occupancy = {}
            if snapshot.exists:
                occupancy = snapshot.to_dict().get("occupancy", {})
            
            # 2. Try to find ONE table that fits everyone first (ideal case)
            best_table = None
            min_remaining_after = float('inf')
            
            # Sort tables by capacity ASC for "Compactness" when single-table booking
            table_configs = sorted(self.TABLE_CONFIG.items(), key=lambda x: x[1]["capacity"])
            
            for table_id, config in table_configs:
                table_data = occupancy.get(table_id, {"booked_pax": 0})
                remaining = config["capacity"] - table_data["booked_pax"]
                if remaining >= pax:
                    remaining_after = remaining - pax
                    if remaining_after < min_remaining_after:
                        min_remaining_after = remaining_after
                        best_table = table_id
                    if remaining_after == 0: break # Perfect fill

            # 3. If no single table fits, use MULTI-TABLE strategy
            assigned_tables = []
            if best_table:
                assigned_tables = [(best_table, pax)]
            else:
                # Sort tables by capacity DESC to keep the group as bunched together as possible
                multi_table_configs = sorted(self.TABLE_CONFIG.items(), key=lambda x: x[1]["capacity"], reverse=True)
                temp_pax = pax
                for table_id, config in multi_table_configs:
                    if temp_pax <= 0: break
                    table_data = occupancy.get(table_id, {"booked_pax": 0})
                    remaining = config["capacity"] - table_data["booked_pax"]
                    
                    if remaining > 0:
                        take = min(temp_pax, remaining)
                        assigned_tables.append((table_id, take))
                        temp_pax -= take
                
                if temp_pax > 0:
                    raise Exception("overbooked")

            # 4. Create the reservation
            primary_table = assigned_tables[0][0]
            all_tables_str = ", ".join([t[0] for t in assigned_tables])
            
            reservation_data = {
                "user_id": user_id,
                "name": name,
                "phone": phone,
                "date": date,
                "time": time,
                "pax": pax,
                "table_id": primary_table, # Keep for backward compatibility
                "all_tables": all_tables_str, # Record all assigned tables
                "status": "confirmed",
                "created_at": firestore.SERVER_TIMESTAMP
            }
            transaction.set(reservation_ref, reservation_data)
            
            # 5. Update occupancy for ALL assigned tables
            for tid, take_pax in assigned_tables:
                if tid not in occupancy:
                    occupancy[tid] = {"booked_pax": 0, "bookings": []}
                if "bookings" not in occupancy[tid]:
                    occupancy[tid]["bookings"] = []
                    
                occupancy[tid]["booked_pax"] += take_pax
                occupancy[tid]["bookings"].append({
                    "res_id": reservation_ref.id,
                    "name": name,
                    "pax": take_pax,
                    "time": time
                })
            
            transaction.set(slot_ref, {"occupancy": occupancy}, merge=True)
            return f"{reservation_ref.id}|{all_tables_str}"

        try:
            result = create_in_transaction(transaction, reservation_ref, slot_ref, date, time, pax, user_id, name, phone)
            return result
        except Exception as e:
            if "overbooked" in str(e):
                return "overbooked"
            logger.error(f"Transaction failed: {e}")
            return None

    async def get_user_reservations(self, user_id: str, include_past: bool = False) -> List[Dict[str, Any]]:
        """
        Get all active reservations for a user.
        By default, only returns future reservations (including today).
        """
        if not self.client:
            return []

        try:
            reservations_ref = self.client.collection("reservations")
            # Filter by user_id
            query = reservations_ref.where("user_id", "==", user_id)
            
            # Execute query
            docs = query.stream()
            reservations = []
            
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                
                # Filter past reservations if not requested
                if not include_past:
                    res_date = data.get('date')
                    if res_date and res_date < today:
                        continue
                        
                reservations.append(data)
                
            # Sort by date and time
            reservations.sort(key=lambda x: (x.get('date', ''), x.get('time', '')))
            
            return reservations
        except Exception as e:
            logger.error(f"Failed to fetch user reservations: {e}")
            return []

    async def get_all_reservations(self, include_past: bool = False) -> List[Dict[str, Any]]:
        """
        Get ALL reservations for admin view.
        """
        if not self.client:
            return []

        try:
            reservations_ref = self.client.collection("reservations")
            docs = reservations_ref.stream()
            reservations = []
            
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                
                # Filter past reservations if not requested
                if not include_past:
                    res_date = data.get('date')
                    if res_date and res_date < today:
                        continue
                        
                reservations.append(data)
                
            # Sort by date and time
            reservations.sort(key=lambda x: (x.get('date', ''), x.get('time', '')))
            return reservations
        except Exception as e:
            logger.error(f"Failed to fetch all reservations: {e}")
            return []

    async def delete_past_reservations(self) -> int:
        """
        Delete all reservations before today.
        Returns the number of deleted documents.
        """
        if not self.client:
            return 0
            
        try:
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            
            reservations_ref = self.client.collection("reservations")
            # Query for dates less than today
            query = reservations_ref.where("date", "<", today)
            docs = query.stream()
            
            count = 0
            batch = self.client.batch()
            
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
                # Firestore batch limit is 500, commit if needed (simplified here)
            
            if count > 0:
                batch.commit()
                logger.info(f"Deleted {count} past reservations.")
                
            return count
        except Exception as e:
            logger.error(f"Failed to delete past reservations: {e}")
            return 0

    async def delete_reservation(self, reservation_id: str) -> bool:
        """
        Delete a reservation by ID and update slot occupancy using a transaction.
        """
        if not self.client:
            return False
            
        transaction = self.client.transaction()
        reservation_ref = self.client.collection("reservations").document(reservation_id)

        @firestore.transactional
        def delete_in_transaction(transaction, reservation_ref):
            snapshot = reservation_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            
            data = snapshot.to_dict()
            date = data.get("date")
            time = data.get("time")
            pax = data.get("pax", 0)
            table_id = data.get("table_id")
            
            # 1. Delete reservation
            transaction.delete(reservation_ref)
            
            # 2. Update slot occupancy
            if date and time:
                slot_id = f"{date}_{time}"
                slot_ref = self.client.collection("slots").document(slot_id)
                slot_snapshot = slot_ref.get(transaction=transaction)
                if slot_snapshot.exists:
                    slot_data = slot_snapshot.to_dict()
                    current_booked = slot_data.get("booked_pax", 0)
                    booked_tables = slot_data.get("tables", [])
                    
                    new_booked = max(0, current_booked - pax)
                    if table_id in booked_tables:
                        booked_tables.remove(table_id)
                    
                    transaction.set(slot_ref, {
                        "booked_pax": new_booked,
                        "tables": booked_tables
                    }, merge=True)
            
            return True

        try:
            if delete_in_transaction(transaction, reservation_ref):
                logger.info(f"Reservation deleted: {reservation_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete reservation in transaction: {e}")
            return False

    async def modify_reservation(self, reservation_id: str, new_date: str, new_time: str, user_id: str, is_admin: bool = False) -> str:
        """
        Modify an existing reservation using a transaction to ensure capacity limits.
        Returns: "success", "unavailable", "not_found", "permission_denied", or "error"
        """
        if not self.client:
            return "error"

        transaction = self.client.transaction()
        reservation_ref = self.client.collection("reservations").document(reservation_id)

        @firestore.transactional
        def modify_in_transaction(transaction, reservation_ref, new_date, new_time, user_id, is_admin):
            snapshot = reservation_ref.get(transaction=transaction)
            if not snapshot.exists:
                return "not_found"
            
            data = snapshot.to_dict()
            if not is_admin and data.get("user_id") != user_id:
                return "permission_denied"
            
            old_date = data.get("date")
            old_time = data.get("time")
            pax = data.get("pax", 0)
            old_table_id = data.get("table_id")

            # If moving within the same slot, just update timestamp
            if old_date == new_date and old_time == new_time:
                transaction.update(reservation_ref, {"updated_at": firestore.SERVER_TIMESTAMP})
                return "success"

            # 1. Allocate new table in new slot
            new_slot_id = f"{new_date}_{new_time}"
            new_slot_ref = self.client.collection("slots").document(new_slot_id)
            new_slot_snapshot = new_slot_ref.get(transaction=transaction)
            
            new_booked_tables = []
            new_booked_pax = 0
            if new_slot_snapshot.exists:
                new_slot_data = new_slot_snapshot.to_dict()
                new_booked_tables = new_slot_data.get("tables", [])
                new_booked_pax = new_slot_data.get("booked_pax", 0)
            
            # Find best table in new slot
            new_table_id = None
            min_diff = float('inf')
            available_tables = sorted(
                [(tid, conf) for tid, conf in self.TABLE_CONFIG.items() if tid not in new_booked_tables],
                key=lambda x: x[1]["capacity"]
            )
            for tid, conf in available_tables:
                if conf["capacity"] >= pax:
                    diff = conf["capacity"] - pax
                    if diff < min_diff:
                        min_diff = diff
                        new_table_id = tid
                    if diff == 0: break
            
            if not new_table_id:
                return "unavailable"

            # 2. Update reservation
            transaction.update(reservation_ref, {
                "date": new_date,
                "time": new_time,
                "table_id": new_table_id,
                "updated_at": firestore.SERVER_TIMESTAMP
            })

            # 3. Update new slot occupancy
            new_booked_tables.append(new_table_id)
            transaction.set(new_slot_ref, {
                "booked_pax": new_booked_pax + pax,
                "tables": new_booked_tables
            }, merge=True)

            # 4. Update old slot occupancy (release old table)
            old_slot_id = f"{old_date}_{old_time}"
            old_slot_ref = self.client.collection("slots").document(old_slot_id)
            old_slot_snapshot = old_slot_ref.get(transaction=transaction)
            if old_slot_snapshot.exists:
                old_slot_data = old_slot_snapshot.to_dict()
                old_booked_pax = old_slot_data.get("booked_pax", 0)
                old_booked_tables = old_slot_data.get("tables", [])
                
                if old_table_id in old_booked_tables:
                    old_booked_tables.remove(old_table_id)
                
                transaction.set(old_slot_ref, {
                    "booked_pax": max(0, old_booked_pax - pax),
                    "tables": old_booked_tables
                }, merge=True)

            return "success"

        try:
            result = modify_in_transaction(transaction, reservation_ref, new_date, new_time, user_id, is_admin)
            if result == "success":
                logger.info(f"Reservation modified: {reservation_id} -> {new_date} {new_time}")
            return result
        except Exception as e:
            logger.error(f"Failed to modify reservation in transaction: {e}")
            return "error"

    async def get_reservation(self, reservation_id: str) -> Optional[Dict[str, Any]]:
        if not self.client:
            return None
        
        try:
            doc = self.client.collection("reservations").document(reservation_id).get()
            if doc.exists:
                return doc.to_dict()
            return None
        except Exception as e:
            logger.error(f"Failed to get reservation: {e}")
            return None

    async def create_order(self, reservation_id: str, items: List[str], total_amount: float) -> str:
        if not self.client:
            return "mock-order-id"

        try:
            order_ref = self.client.collection("orders").document()
            order_data = {
                "reservation_id": reservation_id,
                "items": items,
                "total_amount": total_amount,
                "status": "pending_payment",
                "created_at": firestore.SERVER_TIMESTAMP
            }
            order_ref.set(order_data)
            return order_ref.id
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            return None

    async def get_menu(self) -> List[Dict[str, Any]]:
        """
        Fetch all menu items from Firestore.
        """
        if not self.client:
            # Return mock menu if DB not connected
            return [
                {"name": "Americano", "price": 80, "category": "Coffee"},
                {"name": "Latte", "price": 120, "category": "Coffee"},
                {"name": "Cheese Cake", "price": 120, "category": "Cake"}
            ]
        
        try:
            menu_ref = self.client.collection("menu")
            docs = menu_ref.stream()
            menu_items = []
            for doc in docs:
                item = doc.to_dict()
                item['id'] = doc.id
                menu_items.append(item)
            return menu_items
        except Exception as e:
            logger.error(f"Failed to fetch menu: {e}")
            return []

    async def add_menu_item(self, name: str, price: int, category: str, description: str = "") -> str:
        """
        Add a new item to the menu.
        """
        if not self.client:
            return "mock-id"

        try:
            menu_ref = self.client.collection("menu").document()
            item_data = {
                "name": name,
                "price": price,
                "category": category,
                "description": description,
                "created_at": firestore.SERVER_TIMESTAMP
            }
            menu_ref.set(item_data)
            logger.info(f"Menu item added: {name}")
            return menu_ref.id
        except Exception as e:
            logger.error(f"Failed to add menu item: {e}")
            return None

    async def update_menu_item(self, item_id: str, data: Dict[str, Any]) -> bool:
        """
        Update an existing menu item.
        """
        if not self.client:
            return False

        try:
            menu_ref = self.client.collection("menu").document(item_id)
            menu_ref.update(data)
            logger.info(f"Menu item updated: {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update menu item: {e}")
            return False

    async def delete_menu_item(self, item_id: str) -> bool:
        """
        Delete a menu item.
        """
        if not self.client:
            return False

        try:
            self.client.collection("menu").document(item_id).delete()
            logger.info(f"Menu item deleted: {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete menu item: {e}")
            return False

    async def get_business_hours(self) -> Dict[str, Any]:
        """
        Get business hours configuration.
        """
        if not self.client:
            # Default mock hours
            return {
                "Monday": {"open": "09:00", "close": "18:00", "closed": False},
                "Tuesday": {"open": "09:00", "close": "18:00", "closed": False},
                "Wednesday": {"open": "09:00", "close": "18:00", "closed": False},
                "Thursday": {"open": "09:00", "close": "18:00", "closed": False},
                "Friday": {"open": "09:00", "close": "18:00", "closed": False},
                "Saturday": {"open": "10:00", "close": "20:00", "closed": False},
                "Sunday": {"open": "10:00", "close": "20:00", "closed": False}
            }

        try:
            doc = self.client.collection("config").document("business_hours").get()
            if doc.exists:
                return doc.to_dict()
            else:
                # Initialize with defaults if not exists
                default_hours = {
                    "Monday": {"open": "09:00", "close": "18:00", "closed": False},
                    "Tuesday": {"open": "09:00", "close": "18:00", "closed": False},
                    "Wednesday": {"open": "09:00", "close": "18:00", "closed": False},
                    "Thursday": {"open": "09:00", "close": "18:00", "closed": False},
                    "Friday": {"open": "09:00", "close": "18:00", "closed": False},
                    "Saturday": {"open": "10:00", "close": "20:00", "closed": False},
                    "Sunday": {"open": "10:00", "close": "20:00", "closed": False}
                }
                self.client.collection("config").document("business_hours").set(default_hours)
                return default_hours
        except Exception as e:
            logger.error(f"Failed to get business hours: {e}")
            return {}

    async def get_notification_settings(self) -> Dict[str, Any]:
        """
        Get notification settings (admin IDs).
        """
        if not self.client:
            return {"admin_ids": []}

        try:
            doc = self.client.collection("config").document("notifications").get()
            if doc.exists:
                return doc.to_dict()
            else:
                return {"admin_ids": []}
        except Exception as e:
            logger.error(f"Failed to get notification settings: {e}")
            return {"admin_ids": []}

    async def update_notification_settings(self, admin_ids: List[str]) -> bool:
        """
        Update notification settings.
        """
        if not self.client:
            return False

        try:
            self.client.collection("config").document("notifications").set({"admin_ids": admin_ids})
            logger.info(f"Notification settings updated: {len(admin_ids)} admins.")
            return True
        except Exception as e:
            logger.error(f"Failed to update notification settings: {e}")
            return False

# Singleton instance
db = Database()
