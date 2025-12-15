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
        
        # Command: "GetMyReservations"
        if input_text == "GetMyReservations":
            reservations = await db.get_user_reservations(user_id)
            if not reservations:
                return "You don't have any active reservations."
            
            res_str = "\n".join([f"- ID: {r['id']}, Date: {r['date']}, Time: {r['time']}, Pax: {r['pax']}" for r in reservations])
            return f"Here are your reservations:\n{res_str}"

        # Command: "Modify|ResID|NewDate|NewTime"
        if input_text.startswith("Modify|"):
            try:
                _, res_id, new_date, new_time = input_text.split("|")
                result = await db.modify_reservation(res_id, new_date, new_time, user_id)
                
                if result == "success":
                    return f"Reservation {res_id} modified successfully to {new_date} {new_time}."
                elif result == "unavailable":
                    return f"Sorry, the new time slot {new_date} {new_time} is not available."
                elif result == "permission_denied":
                    return "You do not have permission to modify this reservation."
                elif result == "not_found":
                    return f"Reservation {res_id} not found."
                else:
                    return "An error occurred while modifying the reservation."
            except ValueError:
                return "Error processing modification request."

        # New Format: "Book|YYYY-MM-DD|HH:MM|PAX|Name|Phone"
        if "|" in input_text and input_text.startswith("Book"):
            try:
                _, date, time, pax, name, phone = input_text.split("|")
                pax = int(pax)
                
                is_available = await db.check_availability(date, time, pax)
                if is_available:
                    reservation_id = await db.create_reservation(user_id, date, time, pax, name, phone)
                    return f"Table available! Reservation confirmed for {name}.\nID: {reservation_id}\nTime: {date} {time}\nPax: {pax}"
                else:
                    return "Sorry, no tables available for that time."
            except ValueError:
                return "Error processing reservation data."
        
        # Legacy Format (Regex) - Keep for backward compatibility or direct testing
        match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d+)", input_text)
        
        if match:
            date, time, pax = match.groups()
            pax = int(pax)
            # Use default name/phone for legacy calls
            name = "Guest"
            phone = "Unknown"
            
            is_available = await db.check_availability(date, time, pax)
            if is_available:
                reservation_id = await db.create_reservation(user_id, date, time, pax, name, phone)
                return f"Table available! Reservation confirmed. ID: {reservation_id}"
            else:
                return "Sorry, no tables available for that time."
        else:
            return "Please provide date, time, number of people, name, and phone."

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
                                "pax": {"type": "INTEGER", "description": "Number of people"},
                                "name": {"type": "STRING", "description": "Customer Name"},
                                "phone": {"type": "STRING", "description": "Customer Phone Number"}
                            },
                            "required": ["date", "time", "pax", "name", "phone"]
                        }
                    },
                    {
                        "name": "get_my_reservations",
                        "description": "Get a list of active reservations for the current user.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {},
                            "required": []
                        }
                    },
                    {
                        "name": "modify_reservation",
                        "description": "Modify an existing reservation. Use this when the user wants to change the date or time.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "reservation_id": {"type": "STRING", "description": "The ID of the reservation to modify"},
                                "new_date": {"type": "STRING", "description": "New date (YYYY-MM-DD)"},
                                "new_time": {"type": "STRING", "description": "New time (HH:MM)"}
                            },
                            "required": ["reservation_id", "new_date", "new_time"]
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
        # Simple in-memory history: {user_id: [history]}
        # In production, use Firestore or Redis
        self.chat_histories = {}

    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        logger.info(f"Processing input with LLM: {input_text}")
        user_id = context.get("user_id", "unknown_user")
        
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
        
        # Get current date
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        system_prompt = f"""
        You are a helpful Coffee Shop Assistant at a cafe.
        
        Current Date: {current_date}
        
        {policy_str}
        
        【Current Menu】
        {menu_str}
        
        Instructions:
        1. **Language Consistency**: ALWAYS reply in the SAME language as the user's latest input. If they speak Traditional Chinese, you MUST speak Traditional Chinese.
        2. **Conversational Flow**:
           - If the user says "No" or "Nothing else" (e.g., "沒有了", "沒問題"), politely close the conversation (e.g., "Great! Looking forward to seeing you.", "好的，期待您的光臨！") without asking "Is there anything else?".
           - Only ask "Is there anything else?" if the user's intent is unclear or after completing a task.
        3. **Actions**:
           - If the user wants to Book, Order, or Pay, call the appropriate function.
           - If information is missing (e.g. phone number for booking), ASK for it politely.
        4. **Modifications**:
           - If the user wants to modify a reservation, first use 'get_my_reservations' to show them what they have, then use 'modify_reservation' if they confirm.
        """

        try:
            # Retrieve or initialize chat history
            if user_id not in self.chat_histories:
                self.chat_histories[user_id] = self.model.start_chat(enable_automatic_function_calling=False)
                # Send system prompt as the first message (or just keep it in mind, Gemini API handles history differently)
                # For Gemini SDK, we usually set history in start_chat.
                # Here we will just send the user input.
                # To inject system prompt effectively with history, we might need to prepend it or use system_instruction if supported.
                # For this simple implementation, let's just send the message.
            
            chat = self.chat_histories[user_id]
            
            # We need to inject system prompt context every time or rely on the model remembering.
            # A better way for per-turn context (like dynamic menu) is to include it in the user message,
            # but hidden from the user? No, just prepend it to the prompt.
            
            full_prompt = f"{system_prompt}\n\nUser Input: {input_text}"
            
            response = await chat.send_message_async(full_prompt)
            
            part = response.parts[0]
            
            if part.function_call:
                fc = part.function_call
                func_name = fc.name
                args = fc.args
                
                logger.info(f"LLM decided to call: {func_name} with {args}")
                
                if func_name == "book_table":
                    command = f"Book|{args['date']}|{args['time']}|{int(args['pax'])}|{args['name']}|{args['phone']}"
                    return await self.reservation_agent.process(command, context)
                
                elif func_name == "get_my_reservations":
                    return await self.reservation_agent.process("GetMyReservations", context)
                
                elif func_name == "modify_reservation":
                    command = f"Modify|{args['reservation_id']}|{args['new_date']}|{args['new_time']}"
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
            # Reset history on error to avoid stuck state
            if user_id in self.chat_histories:
                del self.chat_histories[user_id]
            return "Sorry, I'm having trouble understanding. Please try again."

# Singleton instance
conversation_agent = ConversationAgent()
