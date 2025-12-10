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

    async def create_reservation(self, user_id: str, date: str, time: str, pax: int) -> str:
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

    async def seed_menu(self):
        """
        Populate the menu with initial data if empty.
        """
        if not self.client:
            return

        try:
            menu_ref = self.client.collection("menu")
            # Check if empty
            if len(list(menu_ref.limit(1).stream())) > 0:
                logger.info("Menu already exists. Skipping seed.")
                return

            initial_menu = [
                {"name": "Americano", "price": 80, "category": "Coffee", "description": "Classic black coffee"},
                {"name": "Latte", "price": 120, "category": "Coffee", "description": "Espresso with steamed milk"},
                {"name": "Cappuccino", "price": 120, "category": "Coffee", "description": "Espresso with foam"},
                {"name": "Cheese Cake", "price": 120, "category": "Cake", "description": "Rich and creamy"},
                {"name": "Chocolate Cake", "price": 140, "category": "Cake", "description": "Dark chocolate delight"}
            ]

            for item in initial_menu:
                menu_ref.add(item)
            logger.info("Menu seeded successfully.")
        except Exception as e:
            logger.error(f"Failed to seed menu: {e}")

# Singleton instance
db = Database()
