import logging
import os
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from google.cloud import firestore

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

    # ============================================================================
    # MULTI-TENANT: STORE MANAGEMENT
    # ============================================================================

    async def get_store(self, store_id: str) -> Optional[Dict]:
        """Return the stores/{store_id} document, or None."""
        if not self.client:
            return None
        try:
            doc = self.client.collection("stores").document(store_id).get()
            if doc.exists:
                data = doc.to_dict()
                data["store_id"] = doc.id
                return data
            return None
        except Exception as e:
            logger.error(f"Failed to get store {store_id}: {e}")
            return None

    async def get_store_by_destination(self, destination: str) -> Optional[Dict]:
        """Return the stores document whose line_bot_id matches destination, or None."""
        if not self.client:
            return None
        try:
            docs = (
                self.client.collection("stores")
                .where("line_bot_id", "==", destination)
                .limit(1)
                .stream()
            )
            for doc in docs:
                data = doc.to_dict()
                data["store_id"] = doc.id
                return data
            return None
        except Exception as e:
            logger.error(f"Failed to get store by destination: {e}")
            return None

    async def get_store_credentials(self, store_id: str) -> Tuple[str, str]:
        """Return (channel_access_token, channel_secret) from stores/{store_id}."""
        if not self.client:
            return ("", "")
        try:
            doc = self.client.collection("stores").document(store_id).get()
            if doc.exists:
                data = doc.to_dict()
                return (
                    data.get("channel_access_token", ""),
                    data.get("channel_secret", ""),
                )
            return ("", "")
        except Exception as e:
            logger.error(f"Failed to get store credentials: {e}")
            return ("", "")

    async def get_table_config(self, store_id: str) -> Dict:
        """Return table layout from stores/{store_id}/config/table_layout."""
        if not self.client:
            return {"tables": {}, "total_capacity": 0}
        try:
            doc = (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("table_layout")
                .get()
            )
            if doc.exists:
                return doc.to_dict()
            return {"tables": {}, "total_capacity": 0}
        except Exception as e:
            logger.error(f"Failed to get table config: {e}")
            return {"tables": {}, "total_capacity": 0}

    # ============================================================================
    # AVAILABILITY & SEATING
    # ============================================================================

    async def get_daily_occupied_tables(self, store_id: str, date: str) -> Dict[str, Any]:
        """
        Get detailed occupancy for each table on a given date.
        Doc ID format: {store_id}_{date}
        """
        if not self.client:
            return {}
        try:
            slot_ref = self.client.collection("daily_slots").document(f"{store_id}_{date}")
            snapshot = slot_ref.get()
            if snapshot.exists:
                return snapshot.to_dict().get("occupancy", {})
            return {}
        except Exception as e:
            logger.error(f"Error fetching daily table occupancy: {e}")
            return {}

    async def check_availability(self, store_id: str, date: str, time: str, pax: int) -> bool:
        """Check if enough remaining capacity exists across all tables."""
        if not self.client:
            return True
        try:
            closures = await self.get_special_closures(store_id)
            if date in closures:
                return False

            table_config_data = await self.get_table_config(store_id)
            table_config = table_config_data.get("tables", {})
            occupancy = await self.get_daily_occupied_tables(store_id, date)

            total_remaining = 0
            for table_id, config in table_config.items():
                table_data = occupancy.get(table_id, {"booked_pax": 0})
                total_remaining += config["capacity"] - table_data["booked_pax"]

            return total_remaining >= pax
        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return False

    async def get_available_floors(self, store_id: str, date: str, time: str, pax: int) -> List[int]:
        """Returns floors that have enough remaining capacity for the given pax."""
        if not self.client:
            return [1, 2, 3]
        try:
            closures = await self.get_special_closures(store_id)
            if date in closures:
                return []

            table_config_data = await self.get_table_config(store_id)
            table_config = table_config_data.get("tables", {})
            occupancy = await self.get_daily_occupied_tables(store_id, date)

            floor_capacity: Dict[int, int] = {}
            for table_id, config in table_config.items():
                floor = config.get("floor", 1)
                if floor not in floor_capacity:
                    floor_capacity[floor] = 0
                table_data = occupancy.get(table_id, {"booked_pax": 0})
                remaining = max(0, config["capacity"] - table_data["booked_pax"])
                floor_capacity[floor] += remaining

            return sorted([f for f, cap in floor_capacity.items() if cap >= pax])
        except Exception as e:
            logger.error(f"Error checking floor availability: {e}")
            return []

    async def create_reservation(
        self,
        store_id: str,
        user_id: str,
        date: str,
        time: str,
        pax: int,
        name: str,
        phone: str,
        preferred_floor: int = None,
        allow_split_floor: bool = False,
    ) -> str:
        """Create a reservation and allocate seats. Returns '{res_id}|{tables}' on success."""
        if not self.client:
            return "mock-reservation-id"

        # Pre-load table config before the synchronous transaction
        table_config_data = await self.get_table_config(store_id)
        table_config = table_config_data.get("tables", {})

        transaction = self.client.transaction()
        reservation_ref = self.client.collection("reservations").document()
        slot_doc_id = f"{store_id}_{date}"
        slot_ref = self.client.collection("daily_slots").document(slot_doc_id)

        @firestore.transactional
        def create_in_transaction(
            transaction, reservation_ref, slot_ref, date, time, pax,
            user_id, name, phone, preferred_floor, allow_split_floor, table_config
        ):
            snapshot = slot_ref.get(transaction=transaction)
            occupancy = {}
            if snapshot.exists:
                occupancy = snapshot.to_dict().get("occupancy", {})

            # Try to fit everyone on a single table (best-fit)
            best_table = None
            min_remaining_after = float("inf")

            table_items = list(table_config.items())
            if preferred_floor:
                table_items = sorted(table_items, key=lambda x: (x[1]["floor"] != preferred_floor, x[1]["capacity"]))
            else:
                table_items = sorted(table_items, key=lambda x: x[1]["capacity"])

            for table_id, config in table_items:
                table_data = occupancy.get(table_id, {"booked_pax": 0})
                remaining = config["capacity"] - table_data["booked_pax"]
                if remaining >= pax:
                    remaining_after = remaining - pax
                    if preferred_floor and config["floor"] == preferred_floor:
                        best_table = table_id
                        break
                    if remaining_after < min_remaining_after:
                        min_remaining_after = remaining_after
                        best_table = table_id
                    if remaining_after == 0:
                        break

            assigned_tables = []
            if best_table:
                assigned_tables = [(best_table, pax)]
            else:
                # Try to fit on a single floor first
                floors = ([preferred_floor] + [f for f in [2, 3, 1] if f != preferred_floor]) if preferred_floor else [2, 3, 1]
                floor_assigned = False
                for f in floors:
                    temp_assigned = []
                    f_temp_pax = pax
                    f_tables = sorted(
                        [item for item in table_config.items() if item[1]["floor"] == f],
                        key=lambda x: x[1]["capacity"],
                        reverse=True,
                    )
                    for table_id, config in f_tables:
                        if f_temp_pax <= 0:
                            break
                        table_data = occupancy.get(table_id, {"booked_pax": 0})
                        remaining = config["capacity"] - table_data["booked_pax"]
                        if remaining > 0:
                            take = min(f_temp_pax, remaining)
                            temp_assigned.append((table_id, take))
                            f_temp_pax -= take
                    if f_temp_pax <= 0:
                        assigned_tables = temp_assigned
                        floor_assigned = True
                        break

                if not floor_assigned:
                    if not allow_split_floor:
                        raise Exception("split_floor_required")
                    multi_table_configs = sorted(table_config.items(), key=lambda x: x[1]["capacity"], reverse=True)
                    temp_pax = pax
                    for table_id, config in multi_table_configs:
                        if temp_pax <= 0:
                            break
                        table_data = occupancy.get(table_id, {"booked_pax": 0})
                        remaining = config["capacity"] - table_data["booked_pax"]
                        if remaining > 0:
                            take = min(temp_pax, remaining)
                            assigned_tables.append((table_id, take))
                            temp_pax -= take

                if not assigned_tables or sum(t[1] for t in assigned_tables) < pax:
                    raise Exception("overbooked")

            primary_table = assigned_tables[0][0]
            all_tables_str = ", ".join(t[0] for t in assigned_tables)

            reservation_data = {
                "store_id": store_id,
                "user_id": user_id,
                "name": name,
                "phone": phone,
                "date": date,
                "time": time,
                "pax": pax,
                "table_id": primary_table,
                "all_tables": all_tables_str,
                "status": "confirmed",
                "created_at": firestore.SERVER_TIMESTAMP,
            }
            transaction.set(reservation_ref, reservation_data)

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
                    "time": time,
                    "phone_suffix": phone[-4:] if phone else "????",
                })

            transaction.set(slot_ref, {"occupancy": occupancy}, merge=True)
            return f"{reservation_ref.id}|{all_tables_str}"

        try:
            result = create_in_transaction(
                transaction, reservation_ref, slot_ref, date, time, pax,
                user_id, name, phone, preferred_floor, allow_split_floor, table_config
            )
            return result
        except Exception as e:
            if "overbooked" in str(e):
                return "overbooked"
            if "split_floor_required" in str(e):
                return "split_floor_required"
            logger.error(f"Transaction failed: {e}")
            return None

    async def get_user_reservations(self, store_id: str, user_id: str, include_past: bool = False) -> List[Dict[str, Any]]:
        """Get all reservations for a user within a store."""
        if not self.client:
            return []
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            docs = (
                self.client.collection("reservations")
                .where("store_id", "==", store_id)
                .where("user_id", "==", user_id)
                .stream()
            )
            reservations = []
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                if not include_past:
                    if data.get("date", "") < today:
                        continue
                reservations.append(data)
            reservations.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
            return reservations
        except Exception as e:
            logger.error(f"Failed to fetch user reservations: {e}")
            return []

    async def get_all_reservations(self, store_id: str, include_past: bool = False) -> List[Dict[str, Any]]:
        """Get all reservations for a store (admin view)."""
        if not self.client:
            return []
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            docs = (
                self.client.collection("reservations")
                .where("store_id", "==", store_id)
                .stream()
            )
            reservations = []
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                if not include_past:
                    if data.get("date", "") < today:
                        continue
                reservations.append(data)
            reservations.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
            return reservations
        except Exception as e:
            logger.error(f"Failed to fetch all reservations: {e}")
            return []

    async def delete_past_reservations(self, store_id: str) -> int:
        """Delete all past reservations for a store. Returns count deleted."""
        if not self.client:
            return 0
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            docs = (
                self.client.collection("reservations")
                .where("store_id", "==", store_id)
                .where("date", "<", today)
                .stream()
            )
            count = 0
            batch = self.client.batch()
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
            if count > 0:
                batch.commit()
                logger.info(f"Deleted {count} past reservations for store {store_id}.")
            return count
        except Exception as e:
            logger.error(f"Failed to delete past reservations: {e}")
            return 0

    async def delete_reservation(self, store_id: str, reservation_id: str) -> bool:
        """Delete a reservation and release its slots in a transaction."""
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
            all_tables_str = data.get("all_tables") or data.get("table_id", "")

            slot_snapshot = None
            slot_ref = None
            if date:
                slot_doc_id = f"{store_id}_{date}"
                slot_ref = self.client.collection("daily_slots").document(slot_doc_id)
                slot_snapshot = slot_ref.get(transaction=transaction)

            transaction.delete(reservation_ref)

            if slot_snapshot and slot_snapshot.exists:
                occupancy = slot_snapshot.to_dict().get("occupancy", {})
                tables_to_check = [t.strip() for t in all_tables_str.split(",") if t.strip()]
                updated = False
                for tid in tables_to_check:
                    if tid in occupancy:
                        table_data = occupancy[tid]
                        bookings = table_data.get("bookings", [])
                        entry = next((b for b in bookings if b.get("res_id") == reservation_ref.id), None)
                        if entry:
                            pax_to_remove = entry.get("pax", 0)
                            bookings.remove(entry)
                            table_data["booked_pax"] = max(0, table_data.get("booked_pax", 0) - pax_to_remove)
                            table_data["bookings"] = bookings
                            updated = True
                if updated:
                    transaction.update(slot_ref, {"occupancy": occupancy})
            return True

        try:
            if delete_in_transaction(transaction, reservation_ref):
                logger.info(f"Reservation deleted: {reservation_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete reservation in transaction: {e}")
            return False

    async def modify_reservation(
        self, store_id: str, reservation_id: str, new_date: str, new_time: str,
        user_id: str, is_admin: bool = False
    ) -> str:
        """Modify an existing reservation. Returns 'success', 'not_found', 'permission_denied', or 'error'."""
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

            if old_date == new_date and old_time == new_time:
                transaction.update(reservation_ref, {"updated_at": firestore.SERVER_TIMESTAMP})
                return "success"

            # Cross-slot modification: update date/time only; seat re-allocation is a future task
            transaction.update(reservation_ref, {
                "date": new_date,
                "time": new_time,
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            return "success"

        try:
            result = modify_in_transaction(transaction, reservation_ref, new_date, new_time, user_id, is_admin)
            if result == "success":
                logger.info(f"Reservation modified: {reservation_id} -> {new_date} {new_time}")
            return result
        except Exception as e:
            logger.error(f"Failed to modify reservation in transaction: {e}")
            return "error"

    async def get_reservation(self, store_id: str, reservation_id: str) -> Optional[Dict[str, Any]]:
        if not self.client:
            return None
        try:
            doc = self.client.collection("reservations").document(reservation_id).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get("store_id") != store_id:
                    return None
                return data
            return None
        except Exception as e:
            logger.error(f"Failed to get reservation: {e}")
            return None

    async def create_order(self, store_id: str, reservation_id: str, items: List[str], total_amount: float) -> str:
        if not self.client:
            return "mock-order-id"
        try:
            order_ref = self.client.collection("orders").document()
            order_ref.set({
                "store_id": store_id,
                "reservation_id": reservation_id,
                "items": items,
                "total_amount": total_amount,
                "status": "pending_payment",
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            return order_ref.id
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            return None

    # ============================================================================
    # MENU  (stores/{store_id}/menu/)
    # ============================================================================

    async def get_menu(self, store_id: str) -> List[Dict[str, Any]]:
        if not self.client:
            return [
                {"name": "Americano", "price": 80, "category": "Coffee"},
                {"name": "Latte", "price": 120, "category": "Coffee"},
            ]
        try:
            docs = (
                self.client.collection("stores")
                .document(store_id)
                .collection("menu")
                .stream()
            )
            items = []
            for doc in docs:
                item = doc.to_dict()
                item["id"] = doc.id
                items.append(item)
            return items
        except Exception as e:
            logger.error(f"Failed to fetch menu: {e}")
            return []

    async def add_menu_item(self, store_id: str, name: str, price: int, category: str, description: str = "") -> str:
        if not self.client:
            return "mock-id"
        try:
            ref = self.client.collection("stores").document(store_id).collection("menu").document()
            ref.set({
                "name": name,
                "price": price,
                "category": category,
                "description": description,
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            logger.info(f"Menu item added: {name}")
            return ref.id
        except Exception as e:
            logger.error(f"Failed to add menu item: {e}")
            return None

    async def update_menu_item(self, store_id: str, item_id: str, data: Dict[str, Any]) -> bool:
        if not self.client:
            return False
        try:
            self.client.collection("stores").document(store_id).collection("menu").document(item_id).update(data)
            logger.info(f"Menu item updated: {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update menu item: {e}")
            return False

    async def delete_menu_item(self, store_id: str, item_id: str) -> bool:
        if not self.client:
            return False
        try:
            self.client.collection("stores").document(store_id).collection("menu").document(item_id).delete()
            logger.info(f"Menu item deleted: {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete menu item: {e}")
            return False

    # ============================================================================
    # BUSINESS HOURS  (stores/{store_id}/config/business_hours)
    # ============================================================================

    _DEFAULT_HOURS = {
        "Monday":    {"open": "09:00", "close": "18:00", "closed": False},
        "Tuesday":   {"open": "09:00", "close": "18:00", "closed": False},
        "Wednesday": {"open": "09:00", "close": "18:00", "closed": False},
        "Thursday":  {"open": "09:00", "close": "18:00", "closed": False},
        "Friday":    {"open": "09:00", "close": "18:00", "closed": False},
        "Saturday":  {"open": "10:00", "close": "20:00", "closed": False},
        "Sunday":    {"open": "10:00", "close": "20:00", "closed": False},
    }

    async def get_business_hours(self, store_id: str) -> Dict[str, Any]:
        if not self.client:
            return dict(self._DEFAULT_HOURS)
        try:
            doc = (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("business_hours")
                .get()
            )
            if doc.exists:
                return doc.to_dict()
            default = dict(self._DEFAULT_HOURS)
            (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("business_hours")
                .set(default)
            )
            return default
        except Exception as e:
            logger.error(f"Failed to get business hours: {e}")
            return {}

    async def update_business_hours(self, store_id: str, hours: Dict[str, Any]) -> bool:
        if not self.client:
            return False
        try:
            (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("business_hours")
                .set(hours)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update business hours: {e}")
            return False

    # ============================================================================
    # SPECIAL CLOSURES  (stores/{store_id}/config/special_closures)
    # ============================================================================

    async def get_special_closures(self, store_id: str) -> List[str]:
        if not self.client:
            return []
        try:
            doc = (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("special_closures")
                .get()
            )
            if doc.exists:
                return doc.to_dict().get("dates", [])
            return []
        except Exception as e:
            logger.error(f"Failed to get special closures: {e}")
            return []

    async def add_special_closure(self, store_id: str, date: str):
        if not self.client:
            return
        try:
            current = await self.get_special_closures(store_id)
            if date not in current:
                current.append(date)
                (
                    self.client.collection("stores")
                    .document(store_id)
                    .collection("config")
                    .document("special_closures")
                    .set({"dates": current})
                )
        except Exception as e:
            logger.error(f"Failed to add special closure: {e}")

    async def remove_special_closure(self, store_id: str, date: str):
        if not self.client:
            return
        try:
            current = await self.get_special_closures(store_id)
            if date in current:
                current.remove(date)
                (
                    self.client.collection("stores")
                    .document(store_id)
                    .collection("config")
                    .document("special_closures")
                    .set({"dates": current})
                )
        except Exception as e:
            logger.error(f"Failed to remove special closure: {e}")

    # ============================================================================
    # NOTIFICATIONS  (stores/{store_id}/config/notifications)
    # ============================================================================

    async def get_notification_settings(self, store_id: str) -> Dict[str, Any]:
        if not self.client:
            return {"admin_ids": []}
        try:
            doc = (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("notifications")
                .get()
            )
            if doc.exists:
                return doc.to_dict()
            return {"admin_ids": []}
        except Exception as e:
            logger.error(f"Failed to get notification settings: {e}")
            return {"admin_ids": []}

    async def update_notification_settings(self, store_id: str, admin_ids: List[str]) -> bool:
        if not self.client:
            return False
        try:
            (
                self.client.collection("stores")
                .document(store_id)
                .collection("config")
                .document("notifications")
                .set({"admin_ids": admin_ids})
            )
            logger.info(f"Notification settings updated for store {store_id}: {len(admin_ids)} admins.")
            return True
        except Exception as e:
            logger.error(f"Failed to update notification settings: {e}")
            return False

    # ============================================================================
    # CONVERSATION STATE  (conversation_states/{store_id}_{user_id})
    # ============================================================================

    async def get_conversation_state(self, store_id: str, user_id: str) -> Dict[str, Any]:
        if not self.client:
            return {}
        try:
            doc_id = f"{store_id}_{user_id}"
            doc = self.client.collection("conversation_states").document(doc_id).get()
            if doc.exists:
                data = doc.to_dict()
                updated_at = data.get("updated_at")
                if updated_at:
                    from datetime import timedelta, timezone
                    if hasattr(updated_at, "timestamp"):
                        age = datetime.now(timezone.utc) - updated_at
                        if age > timedelta(minutes=30):
                            logger.info(f"[SESSION] State for {doc_id} is stale, clearing")
                            await self.clear_conversation_state(store_id, user_id)
                            return {}
                return data
            return {}
        except Exception as e:
            logger.error(f"Failed to get conversation state: {e}")
            return {}

    async def save_conversation_state(
        self, store_id: str, user_id: str, booking_slot: Dict[str, Any], intent: str = None
    ) -> bool:
        if not self.client:
            return False
        try:
            doc_id = f"{store_id}_{user_id}"
            state_data: Dict[str, Any] = {
                "booking_slot": booking_slot,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if intent:
                state_data["intent"] = intent
            self.client.collection("conversation_states").document(doc_id).set(state_data, merge=True)
            logger.info(f"[SESSION] Saved state for {doc_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save conversation state: {e}")
            return False

    async def clear_conversation_state(self, store_id: str, user_id: str) -> bool:
        if not self.client:
            return False
        try:
            doc_id = f"{store_id}_{user_id}"
            self.client.collection("conversation_states").document(doc_id).delete()
            logger.info(f"[SESSION] Cleared state for {doc_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear conversation state: {e}")
            return False


# Singleton instance
db = Database()
