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

# Singleton instance
db = Database()
