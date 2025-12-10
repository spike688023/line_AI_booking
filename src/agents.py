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
    Uses LLM Function Calling to route user input.
    """
    def __init__(self):
        super().__init__("ConversationAgent")
        self.reservation_agent = ReservationQueryAgent()
        self.order_agent = OrderGenerationAgent()
        self.payment_agent = PaymentStatusAgent()
        
        # Define tools for Gemini
        self.tools = [
            {
                "function_declarations": [
                    {
                        "name": "book_table",
                        "description": "Book a table for a customer. Use this when the user wants to make a reservation.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "date": {"type": "STRING", "description": "Date of reservation (YYYY-MM-DD)"},
                                "time": {"type": "STRING", "description": "Time of reservation (HH:MM)"},
                                "pax": {"type": "INTEGER", "description": "Number of people"}
                            },
                            "required": ["date", "time", "pax"]
                        }
                    },
                    {
                        "name": "order_food",
                        "description": "Place an order for a reservation. Use this when the user wants to order food.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "reservation_id": {"type": "STRING", "description": "The reservation ID"},
                                "items": {"type": "STRING", "description": "Comma separated list of items"}
                            },
                            "required": ["reservation_id", "items"]
                        }
                    },
                    {
                        "name": "check_payment",
                        "description": "Check payment status for an order.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "order_id": {"type": "STRING", "description": "The order ID"}
                            },
                            "required": ["order_id"]
                        }
                    }
                ]
            }
        ]
        
        self.model = genai.GenerativeModel('gemini-2.0-flash', tools=self.tools)

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        logger.info(f"Processing input with LLM: {input_text}")
        
        # Fetch Menu
        menu_items = await db.get_menu()
        menu_str = "\n".join([f"- {item['name']} (${item['price']}): {item.get('category', 'General')}" for item in menu_items])
        
        # Store Policy
        policy_str = """
        【Store Policy】
        1. Minimum charge: $200 per person.
        2. Time limit: 1st Floor is limited to 90 minutes. 2nd and 3rd Floors have no time limit.
        3. No outside food or drinks.
        """
        
        try:
            # Start a chat session to handle function calling
            chat = self.model.start_chat(enable_automatic_function_calling=True)
            
            # Construct System Prompt with Context
            system_prompt = f"""
            You are a helpful Coffee Shop Assistant.
            
            {policy_str}
            
            【Current Menu】
            {menu_str}
            
            User Input: {input_text}
            
            Instructions:
            1. Answer based on the Store Policy and Menu.
            2. If the user wants to perform an action (Book, Order, Pay), call the appropriate function.
            3. If the user is just chatting, reply conversationally.
            4. If the user orders something not on the menu, politely inform them.
            5. Always reply in the same language as the user's input (e.g., Traditional Chinese for Chinese input, English for English input).
            """
            
            # Note: In a real app, we should use 'system_instruction' in model config, 
            # but for per-turn context injection (like dynamic menu), putting it in the prompt is fine.
            
            response = await chat.send_message_async(system_prompt)
            
            # Check if function call happened (Gemini 2.0 might handle this differently in the SDK)
            # With enable_automatic_function_calling=True, the SDK executes the function if we provide the implementation.
            # But here our implementations are in other classes.
            
            # Let's try a different approach: Disable automatic execution and inspect the parts.
            chat = self.model.start_chat(enable_automatic_function_calling=False)
            response = await chat.send_message_async(system_prompt)
            
            part = response.parts[0]
            
            if part.function_call:
                fc = part.function_call
                func_name = fc.name
                args = fc.args
                
                logger.info(f"LLM decided to call: {func_name} with {args}")
                
                if func_name == "book_table":
                    command = f"Book {args['date']} {args['time']} {int(args['pax'])}"
                    return await self.reservation_agent.process(command, context)
                    
                elif func_name == "order_food":
                    command = f"Order {args['reservation_id']} {args['items']}"
                    return await self.order_agent.process(command, context)
                    
                elif func_name == "check_payment":
                    command = f"Pay {args['order_id']}"
                    return await self.payment_agent.process(command, context)
            
            # If no function call, return the text
            return response.text

        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return "Sorry, I'm having trouble understanding. Please try again."

# Singleton instance
conversation_agent = ConversationAgent()
