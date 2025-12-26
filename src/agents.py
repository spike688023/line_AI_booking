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
    def __init__(self, database=None):
        super().__init__("ReservationQueryAgent")
        self.db = database if database else db

    async def check_availability(self, date: str, time: str, pax: int):
        """Check if a table is available."""
        # 1. Check if the date is in the past
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        if date < current_date:
            return "Error: Cannot book for a past date."
            
        # 1.5 Check Special Closures (Holidays)
        special_closures = await self.db.get_special_closures()
        if date in special_closures:
            return "Sorry, we are closed on this date for a special holiday/event."

        # 2. Check Business Hours
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            day_name = dt.strftime("%A") # e.g., "Monday"
            
            hours = await self.db.get_business_hours()
            day_config = hours.get(day_name)
            
            if not day_config or day_config.get("closed"):
                return False # Closed on this day
            
            open_time = day_config.get("open", "09:00")
            close_time = day_config.get("close", "18:00")
            
            if not (open_time <= time <= close_time):
                return False # Outside business hours
                
        except ValueError:
            pass # Invalid date format, let DB handle or fail later

        # 2. Check Table Availability
        is_available = await self.db.check_availability(date, time, pax)
        return "Table is available" if is_available else "Table is not available"

    async def book_table(self, date: str, time: str, pax: int, name: str, phone: str, floor: int = None, context: Dict[str, Any] = None):
        """Book a table."""
        # Check if the date is in the past
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        if date < current_date:
            return "Error: Cannot book for a past date."
            
        user_id = context.get("user_id", "unknown")
        result = await self.db.create_reservation(user_id, date, time, pax, name, phone, preferred_floor=floor)
        
        if result == "overbooked":
            return "Sorry, that time slot is now full. Please choose another time."
        elif not result:
            return "Sorry, something went wrong with the reservation. Please try again later."
            
        # Parse result (format: "reservation_id|table_id")
        parts = result.split("|")
        res_id = parts[0]
        table_str = parts[1] if len(parts) > 1 else "Unknown"
        
        # Format table info
        table_display = table_str
        seating_note = ""
        if "," in table_str:
            tables = table_str.split(", ")
            count = len(tables)
            table_display = f"{table_str} (共{count}桌)"
            seating_note = f"\n⚠️ 座位說明：依據人數為您安排了 {count} 張鄰近桌位，皆位於同一樓層。"

        # Construct a nice Chinese confirmation message
        message = (
            f"🎉 預約成功！\n\n"
            f"📅 日期：{date}\n"
            f"⏰ 抵達時間：{time}\n"
            f"👥 人數：{pax} 位\n"
            f"📍 安排桌號：{table_display}\n"
            f"🆔 訂單編號：{res_id}\n"
            f"{seating_note}\n"
            f"🗺️ 查看座位位置：\n"
            f"https://coffee-shop-agent-416902381938.asia-east1.run.app/seating-map?date={date}\n\n"
            f"💡 溫馨提示：本店不限用餐時間。若該桌位較大，可能會與其他客人共享桌位，感謝您的理解！"
        )
        return message

    async def get_my_reservations(self, include_past: bool = False, context: Dict[str, Any] = None):
        """Get user's reservations."""
        user_id = context.get("user_id", "unknown")
        reservations = await self.db.get_user_reservations(user_id, include_past=include_past)
        
        if not reservations:
            return "You have no reservations."
        
        return "\n".join([f"{r['date']} {r['time']} ({r['pax']} pax)" for r in reservations])

    async def modify_reservation(self, reservation_id: str, new_date: str, new_time: str, context: Dict[str, Any] = None):
        """Modify a reservation."""
        user_id = context.get("user_id", "unknown")
        result = await self.db.modify_reservation(reservation_id, new_date, new_time, user_id)
        
        if result == "success":
            return "Reservation modified successfully."
        elif result == "unavailable":
            return "New time slot is unavailable."
        elif result == "permission_denied":
            return "Permission denied."
        else:
            return "Failed to modify reservation."

    async def process(self, input_text: str, context: Dict[str, Any] = None, language: str = "zh-TW") -> str:
        user_id = context.get("user_id", "unknown_user")
        
        # Helper for multilingual response
        def get_msg(key, **kwargs):
            messages = {
                "zh-TW": {
                    "no_reservations": "您目前沒有任何有效的訂位。",
                    "reservations_list": "這是您的訂位紀錄：\n{res_str}",
                    "modify_success": "訂位 {res_id} 已成功修改為 {new_date} {new_time}。",
                    "modify_unavailable": "抱歉，新的時段 {new_date} {new_time} 已經客滿了。",
                    "permission_denied": "您沒有權限修改此訂位。",
                    "not_found": "找不到訂位 {res_id}。",
                    "modify_error": "修改訂位時發生錯誤。",
                    "process_error": "處理請求時發生錯誤。",
                    "book_success": "有位子！已為您確認訂位。\n大名: {name}\nID: {reservation_id}\n時間: {date} {time}\n人數: {pax}",
                    "book_unavailable": "抱歉，該時段已經客滿了。",
                    "missing_info": "請提供日期、時間、人數、大名和電話。"
                },
                "en": {
                    "no_reservations": "You don't have any active reservations.",
                    "reservations_list": "Here are your reservations:\n{res_str}",
                    "modify_success": "Reservation {res_id} modified successfully to {new_date} {new_time}.",
                    "modify_unavailable": "Sorry, the new time slot {new_date} {new_time} is not available.",
                    "permission_denied": "You do not have permission to modify this reservation.",
                    "not_found": "Reservation {res_id} not found.",
                    "modify_error": "An error occurred while modifying the reservation.",
                    "process_error": "Error processing request.",
                    "book_success": "Table available! Reservation confirmed for {name}.\nID: {reservation_id}\nTime: {date} {time}\nPax: {pax}",
                    "book_unavailable": "Sorry, no tables available for that time.",
                    "missing_info": "Please provide date, time, number of people, name, and phone."
                }
            }
            # Default to English if language not supported
            lang_msgs = messages.get(language, messages["en"])
            return lang_msgs.get(key, "").format(**kwargs)

        # Command: "GetMyReservations|include_past" or just "GetMyReservations"
        if input_text.startswith("GetMyReservations"):
            include_past = False
            if "|" in input_text:
                parts = input_text.split("|")
                if len(parts) > 1 and parts[1] == "True":
                    include_past = True
            
            reservations = await self.db.get_user_reservations(user_id, include_past=include_past)
            if not reservations:
                return get_msg("no_reservations")
            
            if language == "zh-TW":
                res_str = "\n\n".join([f"📍 訂位 ID: {r['id']}\n   📅 日期: {r['date']}\n   ⏰ 時間: {r['time']}\n   👥 人數: {r['pax']}\n   👤 姓名: {r.get('name', 'N/A')}\n   📞 電話: {r.get('phone', 'N/A')}" for r in reservations])
            else:
                res_str = "\n\n".join([f"📍 ID: {r['id']}\n   📅 Date: {r['date']}\n   ⏰ Time: {r['time']}\n   👥 Pax: {r['pax']}\n   👤 Name: {r.get('name', 'N/A')}\n   📞 Phone: {r.get('phone', 'N/A')}" for r in reservations])
            
            return get_msg("reservations_list", res_str=res_str)

        # Command: "Modify|ResID|NewDate|NewTime"
        if input_text.startswith("Modify|"):
            try:
                _, res_id, new_date, new_time = input_text.split("|")
                result = await self.db.modify_reservation(res_id, new_date, new_time, user_id)
                
                if result == "success":
                    return get_msg("modify_success", res_id=res_id, new_date=new_date, new_time=new_time)
                elif result == "unavailable":
                    return get_msg("modify_unavailable", new_date=new_date, new_time=new_time)
                elif result == "permission_denied":
                    return get_msg("permission_denied")
                elif result == "not_found":
                    return get_msg("not_found", res_id=res_id)
                else:
                    return get_msg("modify_error")
            except ValueError:
                return get_msg("process_error")

        # New Format: "Book|YYYY-MM-DD|HH:MM|PAX|Name|Phone"
        if "|" in input_text and input_text.startswith("Book"):
            try:
                _, date, time, pax, name, phone = input_text.split("|")
                pax = int(pax)
                
                is_available = await self.db.check_availability(date, time, pax)
                if is_available:
                    res_result = await self.db.create_reservation(user_id, date, time, pax, name, phone)
                    
                    # Parse result: "res_id|table1, table2..."
                    if "|" in res_result:
                        reservation_id, table_str = res_result.split("|")
                    else:
                        reservation_id = res_result
                        table_str = ""

                    # Generate Seating Explanation
                    seating_note = ""
                    if table_str:
                        tables = table_str.split(", ")
                        if len(tables) > 1:
                            # Split seating logic
                            seating_note = f"\n\n⚠️ 座位安排說明：由於人多，我們為您安排了 {len(tables)} 張桌子 ({table_str})，都在同一樓層，請不用擔心。"
                        else:
                            seating_note = f"\n桌號: {tables[0]}"

                    msg = get_msg("book_success", name=name, reservation_id=reservation_id, date=date, time=time, pax=pax)
                    return msg + seating_note
                else:
                    return get_msg("book_unavailable")
            except ValueError:
                return get_msg("process_error")
        
        # Legacy Format (Regex)
        match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d+)", input_text)
        
        if match:
            date, time, pax = match.groups()
            pax = int(pax)
            name = "Guest"
            phone = "Unknown"
            
            is_available = await self.db.check_availability(date, time, pax)
            if is_available:
                reservation_id = await self.db.create_reservation(user_id, date, time, pax, name, phone)
                return get_msg("book_success", name=name, reservation_id=reservation_id, date=date, time=time, pax=pax)
            else:
                return get_msg("book_unavailable")
        
        # LLM Fallback
        try:
            # Send message to LLM
            response = await self.chat.send_message_async(input_text)
            
            # Check for function call
            if response.parts and response.parts[0].function_call:
                fc = response.parts[0].function_call
                func_name = fc.name
                args = fc.args
                
                # Execute the function
                if func_name == "book_table":
                    return await self.book_table(
                        date=args["date"],
                        time=args["time"],
                        pax=int(args["pax"]),
                        name=args["name"],
                        phone=args["phone"],
                        floor=int(args.get("floor")) if args.get("floor") else None,
                        context=context
                    )
                elif func_name == "get_my_reservations":
                    return await self.get_my_reservations(
                        include_past=args.get("include_past", False),
                        context=context
                    )
                elif func_name == "modify_reservation":
                    return await self.modify_reservation(
                        reservation_id=args["reservation_id"],
                        new_date=args["new_date"],
                        new_time=args["new_time"],
                        context=context
                    )
                elif func_name == "check_availability":
                    return await self.check_availability(
                        date=args["date"],
                        time=args["time"],
                        pax=int(args["pax"])
                    )
            
            # Return text response if no function call
            return response.text if response.text else get_msg("process_error")
            
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return get_msg("process_error")

class OrderGenerationAgent(BaseAgent):
    """
    Agent responsible for creating reservation orders.
    """
    def __init__(self):
        super().__init__("OrderGenerationAgent")

    async def process(self, input_text: str, context: Dict[str, Any] = None, language: str = "zh-TW") -> str:
        # Expected format: "Order [ReservationID] [Item1, Item2]"
        
        parts = input_text.split(" ", 2)
        if len(parts) < 3:
             return "請提供訂位 ID 和餐點項目。格式：Order [ID] [Items]" if language == "zh-TW" else "Please provide Reservation ID and Items. Format: Order [ID] [Items]"
        
        reservation_id = parts[1]
        items_str = parts[2]
        items = [item.strip() for item in items_str.split(",")]
        
        # Mock price calculation
        total_amount = len(items) * 10.0 
        
        order_id = await db.create_order(reservation_id, items, total_amount)
        if language == "zh-TW":
            return f"訂單已建立！ID: {order_id}。總金額: ${total_amount}。請前往付款。"
        else:
            return f"Order created! ID: {order_id}. Total: ${total_amount}. Please proceed to payment."

class PaymentStatusAgent(BaseAgent):
    """
    Agent responsible for verifying payment status.
    """
    def __init__(self):
        super().__init__("PaymentStatusAgent")

    async def process(self, input_text: str, context: Dict[str, Any] = None, language: str = "zh-TW") -> str:
        if language == "zh-TW":
            return f"付款狀態已確認。沒問題！(模擬)"
        else:
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
                                "phone": {"type": "STRING", "description": "Customer Phone Number"},
                                "floor": {"type": "INTEGER", "description": "Preferred floor (1, 2, or 3). Optional."}
                            },
                            "required": ["date", "time", "pax", "name", "phone"]
                        }
                    },
                    {
                        "name": "get_my_reservations",
                        "description": "Get a list of reservations for the current user. By default returns only future reservations.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "include_past": {"type": "BOOLEAN", "description": "Set to true to include past reservations (history)."}
                            },
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
        # Fetch Business Hours
        hours_config = await db.get_business_hours()
        hours_str = "Business Hours:\n"
        for day, config in hours_config.items():
            if config.get("closed"):
                hours_str += f"- {day}: Closed\n"
            else:
                hours_str += f"- {day}: {config.get('open')} - {config.get('close')}\n"

        policy_str = f"""
        【Store Policy】
        1. 1st Floor: Time limit 90 minutes. Minimum charge $200 per person.
        2. 2nd Floor: No time limit (suitable for conversations). Minimum charge $200 per person.
        3. No outside food or drinks.
        
        【Business Hours】
        {hours_str}
        
        【Seating Information】
        - Total Capacity: ~40 seats across 3 floors.
        - 1F: Bar counter (2), 4-p table (1), 2-p table (1).
        - 2F: Large 6-p table (1), 4-p tables (2), 1-p quiet seats (4). (Restroom available)
        - 3F: Large 6-p table (1), 4-p tables (3), 1-p quiet seats (4).
        
        【Booking Policy for Large Groups】
        - We welcome large groups! If the group size exceeds our largest table (6 people), our system will automatically allocate multiple adjacent tables on the SAME FLOOR to keep your group together.
        - The interactive seating map will show the exact table arrangement.
        """
        
        # Get current date
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Fetch User Info from past reservations to avoid asking again
        user_info_str = ""
        try:
            past_reservations = await db.get_user_reservations(user_id, include_past=True)
            if past_reservations:
                # Use the most recent entry that has name/phone
                latest_with_name = next((r for r in reversed(past_reservations) if r.get("name")), None)
                latest_with_phone = next((r for r in reversed(past_reservations) if r.get("phone")), None)
                u_name = latest_with_name.get("name") if latest_with_name else None
                u_phone = latest_with_phone.get("phone") if latest_with_phone else None
                
                if u_name or u_phone:
                    user_info_str = "【Known User Information】\n"
                    if u_name: user_info_str += f"- Name: {u_name}\n"
                    if u_phone: user_info_str += f"- Phone: {u_phone}\n"
        except Exception as e:
            logger.error(f"Error fetching user info for context: {e}")

        system_prompt = f"""
        You are a helpful Coffee Shop Assistant at a cafe.
        
        Current Date: {current_date}
        
        {user_info_str}
        
        {policy_str}
        
        【Current Menu】
        {menu_str}
        
        Instructions:
        1. Language Consistency: ALWAYS reply in the SAME language as the user's latest input. If they speak Traditional Chinese, you MUST speak Traditional Chinese.
        2. Date Validation: DO NOT accept reservations for dates before {current_date}. If a user asks for a past date, politely explain that we can only take future bookings.
        3. Conversational Flow:
           - If the user says "No" or "Nothing else" (e.g., "沒有了", "沒問題"), politely close the conversation (e.g., "Great! Looking forward to seeing you.", "好的，期待您的光臨！") without asking "Is there anything else?".
           - Only ask "Is there anything else?" if the user's intent is unclear or after completing a task.
        4. User Identity (AVOID REPETITION):
           - If you already have the user's Name and Phone (from 【Known User Information】 or the chat history), DO NOT ask for them again. 
           - You can simply confirm like: "好的，依然是陳先生您的訂位嗎？" or use them directly.
        5. Actions:
           - If the user wants to Book, Order, or Pay, call the appropriate function.
           - If information is missing (e.g. phone number for booking), ASK for it politely.
        6. Modifications:
           - If the user wants to modify a reservation, first use 'get_my_reservations' to show them what they have, then use 'modify_reservation' if they confirm.
        7. Split Seating Warning:
           - If a booking is for more than 6 people, you MUST inform the user that they will be split across multiple tables.
           - Reassure them that the tables will be on the SAME FLOOR to keep the group together.
           - Example: "由於您的團體人數較多（8位），我們將為您安排在同一樓層的兩張相鄰桌位，方便您們互相照應。"
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
            
            # Detect language (Simple heuristic or ask LLM)
            # For now, we can infer it from the user input or just default to zh-TW
            # But since we want the LLM to control it, we can add a 'language' parameter to the tools definition?
            # Or, simpler: The LLM instructions say "Reply in the same language".
            # We can detect if the input is mostly English.
            
            def is_english(text):
                try:
                    text.encode(encoding='utf-8').decode('ascii')
                except UnicodeDecodeError:
                    return False
                return True

            current_lang = "en" if is_english(input_text) else "zh-TW"
            
            if part.function_call:
                fc = part.function_call
                func_name = fc.name
                args = fc.args
                
                logger.info(f"LLM decided to call: {func_name} with {args}")
                
                if func_name == "book_table":
                    command = f"Book|{args['date']}|{args['time']}|{int(args['pax'])}|{args['name']}|{args['phone']}"
                    result = await self.reservation_agent.process(command, context, language=current_lang)
                    
                    # Send notification if successful
                    if "ID:" in result:
                         # Extract details for notification (simplified)
                         from app import send_admin_notification
                         await send_admin_notification(f"New Reservation!\n{args['name']} ({args['pax']} pax)\n{args['date']} {args['time']}")
                    
                    return result
                
                elif func_name == "get_my_reservations":
                    include_past = args.get("include_past", False)
                    command = f"GetMyReservations|{include_past}"
                    return await self.reservation_agent.process(command, context, language=current_lang)
                
                elif func_name == "modify_reservation":
                    command = f"Modify|{args['reservation_id']}|{args['new_date']}|{args['new_time']}"
                    return await self.reservation_agent.process(command, context, language=current_lang)
                    
                elif func_name == "order_food":
                    command = f"Order {args['reservation_id']} {args['items']}"
                    return await self.order_agent.process(command, context, language=current_lang)
                    
                elif func_name == "check_payment":
                    command = f"Pay {args['order_id']}"
                    return await self.payment_agent.process(command, context, language=current_lang)
            
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
