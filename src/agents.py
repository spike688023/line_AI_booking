import logging
import re
import os
from typing import List, Dict, Any
import google.generativeai as genai
from src.database import db

# Configure Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    logging.warning("GOOGLE_API_KEY not set. LLM features will not work.")

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for all agents."""
    def __init__(self, name: str):
        self.name = name
        logger.info(f"Initialized Agent: {name}")

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        raise NotImplementedError

class ReservationQueryAgent(BaseAgent):
    """
    Agent responsible for checking table availability and creating reservations.
    """
    def __init__(self):
        super().__init__("ReservationQueryAgent")

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        user_id = context.get("user_id", "unknown_user")
        
        # Simple regex to extract date, time, pax
        # Expected format: "Book 2023-10-27 18:00 4"
        match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d+)", input_text)
        
        if match:
            date, time, pax = match.groups()
            pax = int(pax)
            
            is_available = await db.check_availability(date, time, pax)
            if is_available:
                reservation_id = await db.create_reservation(user_id, date, time, pax)
                return f"Table available! Reservation confirmed. ID: {reservation_id}"
            else:
                return "Sorry, no tables available for that time."
        else:
            return "Please provide date, time, and number of people. Format: YYYY-MM-DD HH:MM PAX (e.g., 2023-10-27 18:00 4)"

class OrderGenerationAgent(BaseAgent):
    """
    Agent responsible for creating reservation orders.
    """
    def __init__(self):
        super().__init__("OrderGenerationAgent")

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        # Expected format: "Order [ReservationID] [Item1, Item2]"
        # Example: "Order res123 Coffee, Cake"
        
        parts = input_text.split(" ", 2)
        if len(parts) < 3:
             return "Please provide Reservation ID and Items. Format: Order [ID] [Items]"
        
        reservation_id = parts[1]
        items_str = parts[2]
        items = [item.strip() for item in items_str.split(",")]
        
        # Mock price calculation
        total_amount = len(items) * 10.0 
        
        order_id = await db.create_order(reservation_id, items, total_amount)
        return f"Order created! ID: {order_id}. Total: ${total_amount}. Please proceed to payment."

class PaymentStatusAgent(BaseAgent):
    """
    Agent responsible for verifying payment status.
    """
    def __init__(self):
        super().__init__("PaymentStatusAgent")

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        # Mock payment check
        return f"Payment status checked. All good! (Mock)"

class ConversationAgent(BaseAgent):
    """
    Main Orchestrator Agent.
    Routes user input to the appropriate sub-agent.
    """
    def __init__(self):
        super().__init__("ConversationAgent")
        self.reservation_agent = ReservationQueryAgent()
        self.order_agent = OrderGenerationAgent()
        self.payment_agent = PaymentStatusAgent()
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        """
        Main entry point.
        1. Identify intent.
        2. Route to sub-agent.
        3. Return response.
        """
        logger.info(f"Processing input: {input_text}")
        
        # Simple keyword-based routing
        input_lower = input_text.lower()
        
        if "book" in input_lower or "reserve" in input_lower or "訂位" in input_lower:
            return await self.reservation_agent.process(input_text, context)
        elif "order" in input_lower or "confirm" in input_lower or "下單" in input_lower:
            return await self.order_agent.process(input_text, context)
        elif "pay" in input_lower or "status" in input_lower or "付款" in input_lower:
            return await self.payment_agent.process(input_text, context)
        else:
            # Fallback to LLM
            try:
                prompt = f"""You are a helpful assistant for a Coffee Shop. 
                The user said: "{input_text}"
                
                If they are asking about the menu, recommend our signature Coffee and Cake.
                If they are just saying hi, greet them warmly.
                If they seem confused, explain that you can help them Book a table, Order food, or Check payment status.
                
                Keep the response concise and friendly.
                """
                response = await self.model.generate_content_async(prompt)
                return response.text
            except Exception as e:
                logger.error(f"LLM Error: {e}")
                return "Welcome to Coffee Shop! \n1. To book: 'Book YYYY-MM-DD HH:MM PAX'\n2. To order: 'Order [ResID] [Items]'\n3. Payment status: 'Pay [OrderID]'"

# Singleton instance
conversation_agent = ConversationAgent()
