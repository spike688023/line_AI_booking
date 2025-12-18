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
            if self.project_id:
                self.client = firestore.Client(project=self.project_id)
                logger.info(f"Firestore client initialized for project: {self.project_id}")
            else:
                logger.warning("GOOGLE_CLOUD_PROJECT not set. Firestore client not initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}")

    async def check_availability(self, date: str, time: str, pax: int) -> bool:
        """
        Check if a table is available for the given date, time, and pax.
        This is a simplified logic. In a real app, you'd check against total capacity.
        """
        if not self.client:
            logger.warning("Firestore client not available. Returning mock availability.")
            return True

        # Example: Check if total reservations for that slot < capacity
        # For now, just return True
        return True

    async def create_reservation(self, user_id: str, date: str, time: str, pax: int, name: str, phone: str) -> str:
        """
        Create a new reservation.
        """
        if not self.client:
            logger.warning("Firestore client not available. Returning mock reservation ID.")
            return "mock-reservation-id"

        try:
            reservation_ref = self.client.collection("reservations").document()
            reservation_data = {
                "user_id": user_id,
                "name": name,
                "phone": phone,
                "date": date,
                "time": time,
                "pax": pax,
                "status": "confirmed",
                "created_at": firestore.SERVER_TIMESTAMP
            }
            reservation_ref.set(reservation_data)
            logger.info(f"Reservation created: {reservation_ref.id}")
            return reservation_ref.id
        except Exception as e:
            logger.error(f"Failed to create reservation: {e}")
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
        Delete a reservation by ID.
        """
        if not self.client:
            return False
            
        try:
            self.client.collection("reservations").document(reservation_id).delete()
            logger.info(f"Reservation deleted: {reservation_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete reservation: {e}")
            return False

    async def modify_reservation(self, reservation_id: str, new_date: str, new_time: str, user_id: str, is_admin: bool = False) -> str:
        """
        Modify an existing reservation. Checks availability and ownership first.
        Returns: "success", "unavailable", "not_found", "permission_denied", or "error"
        """
        if not self.client:
            return "error"

        try:
            reservation_ref = self.client.collection("reservations").document(reservation_id)
            doc = reservation_ref.get()
            
            if not doc.exists:
                return "not_found"
            
            data = doc.to_dict()
            
            # Ownership Check (Skip if admin)
            if not is_admin and data.get("user_id") != user_id:
                logger.warning(f"Unauthorized modification attempt by {user_id} on {reservation_id}")
                return "permission_denied"
            
            pax = data.get("pax", 0)
            
            # Check availability for new slot
            # Note: Admin might want to force overbook, but for now let's keep availability check
            is_available = await self.check_availability(new_date, new_time, pax)
            if not is_available:
                return "unavailable"
            
            # Update reservation
            reservation_ref.update({
                "date": new_date,
                "time": new_time,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Reservation modified: {reservation_id} -> {new_date} {new_time}")
            return "success"
            
        except Exception as e:
            logger.error(f"Failed to modify reservation: {e}")
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
