"""
LangGraph-based Agent Architecture for Coffee Shop Booking System

This module implements a state machine approach to handle user conversations,
replacing the monolithic prompt-based system with modular, controllable nodes.
"""

import logging
import os
from typing import TypedDict, Annotated, List, Dict, Any, Literal
from datetime import datetime
import operator
import re

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from src.database import db

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================================
# STATE DEFINITION
# ============================================================================

class AgentState(TypedDict):
    """
    Central state object that flows through all nodes.
    This replaces implicit chat history with explicit state tracking.
    """
    # Conversation context
    messages: Annotated[List[Any], operator.add]
    
    # User information
    user_id: str
    user_profile: Dict[str, Any]  # {name, phone, past_reservations}
    
    # Intent classification
    intent: Literal["booking", "query_menu", "modify_reservation", "general_chat", "unclear", "unknown"]
    
    # Booking slot filling (for reservation flow)
    booking_slot: Dict[str, Any]  # {date, time, pax, name, phone, floor, allow_split}
    
    # Flow control
    next_step: str  # "ask_date", "ask_pax", "validate", "execute", "respond"
    error_msg: str  # Error from previous step (e.g., "Date is in the past")
    
    # Language preference (detected from user input)
    language: Literal["zh-TW", "en"]
    
    # Final response to user
    final_response: str


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def detect_language(text: str) -> Literal["zh-TW", "en"]:
    """Simple language detection based on character encoding."""
    try:
        text.encode('ascii')
        return "en"
    except UnicodeEncodeError:
        return "zh-TW"


def get_missing_booking_fields(booking_slot: Dict[str, Any]) -> List[str]:
    """Check which required fields are missing from booking slot."""
    required = ["date", "time", "pax", "name", "phone"]
    return [field for field in required if not booking_slot.get(field)]


# ============================================================================
# NODE IMPLEMENTATIONS
# ============================================================================

async def router_node(state: AgentState) -> AgentState:
    """
    Entry point: Classify user intent.

    Hybrid approach:
    1. Fast keyword/regex matching for obvious cases (free, instant)
    2. LLM fallback for ambiguous inputs (smart, but costs API call)
    """
    logger.info("[ROUTER] Classifying user intent...")

    user_input = state["messages"][-1].content if state["messages"] else ""

    # === PHASE 1: Fast keyword/regex matching ===
    keywords_booking = ["訂位", "預約", "book", "reservation", "reserve", "訂"]
    keywords_menu = ["菜單", "menu", "餐點", "food", "drink", "價格", "price"]
    keywords_modify = ["修改", "取消", "改", "change", "modify", "cancel"]

    intent = None  # None means "not sure yet"

    # Check for explicit booking keywords
    if any(kw in user_input for kw in keywords_booking):
        intent = "booking"
    # Check for date/time/pax patterns (implicit booking intent)
    elif re.search(r'\d{4}-\d{2}-\d{2}', user_input):  # Full date: 2026-02-07
        intent = "booking"
    elif re.search(r'\d{1,2}/\d{1,2}', user_input):  # Short date: 2/7, 02/07
        intent = "booking"
    elif re.search(r'\d{1,2}月\d{1,2}日?', user_input):  # Chinese date: 2月7日
        intent = "booking"
    elif re.search(r'\d{1,2}:\d{2}', user_input):  # Time pattern
        intent = "booking"
    elif re.search(r'(明天|後天|下週|next|tomorrow|今天|today)', user_input):  # Relative date
        intent = "booking"
    elif re.search(r'(\d+)\s*(個人|人|位|pax|people|guests?)', user_input):  # Pax pattern
        intent = "booking"
    elif any(kw in user_input for kw in keywords_menu):
        intent = "query_menu"
    elif any(kw in user_input for kw in keywords_modify):
        intent = "modify_reservation"

    # === PHASE 2: LLM fallback for ambiguous inputs ===
    if intent is None:
        logger.info("[ROUTER] No keyword match, using LLM classification...")
        intent = await _classify_intent_with_llm(user_input)

    state["intent"] = intent
    logger.info(f"[ROUTER] Detected intent: {intent}")

    return state


async def _classify_intent_with_llm(user_input: str) -> str:
    """
    Use LLM to classify intent when keyword matching fails.
    Returns one of: booking, query_menu, modify_reservation, general_chat, unclear
    """
    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=os.getenv("GOOGLE_API_KEY")
        )

        prompt = f"""你是咖啡廳訂位系統的意圖分類器。

根據用戶輸入，判斷意圖類別。只回答一個英文詞，不要解釋。

類別：
- booking: 想訂位、約位子、安排座位、帶朋友來、想去店裡
- query_menu: 問菜單、餐點、飲料、價格
- modify_reservation: 修改或取消訂位
- general_chat: 打招呼、閒聊、問候語（如「你好」「嗨」）
- unclear: 無法判斷、訊息太短或太模糊（如單一符號、亂碼、無意義文字）

用戶說：「{user_input}」

意圖："""

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_intent = response.content.strip().lower()

        # Validate and normalize the response
        valid_intents = ["booking", "query_menu", "modify_reservation", "general_chat", "unclear"]
        for valid in valid_intents:
            if valid in raw_intent:
                logger.info(f"[ROUTER] LLM classified as: {valid}")
                return valid

        # Default to unclear if LLM gives unexpected response
        logger.warning(f"[ROUTER] LLM returned unexpected: {raw_intent}, defaulting to unclear")
        return "unclear"

    except Exception as e:
        logger.error(f"[ROUTER] LLM classification failed: {e}")
        return "unclear"


async def booking_collector_node(state: AgentState) -> AgentState:
    """
    Extract booking information from user input using LLM.
    This node ONLY extracts data, does NOT generate responses.
    """
    logger.info("[COLLECTOR] Extracting booking information...")
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY")
    )
    
    user_input = state["messages"][-1].content if state["messages"] else ""
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Fetch user profile for context (avoid re-asking)
    user_profile = {}
    try:
        past_reservations = await db.get_user_reservations(state["user_id"], include_past=True)
        if past_reservations:
            latest_with_name = next((r for r in reversed(past_reservations) if r.get("name")), None)
            latest_with_phone = next((r for r in reversed(past_reservations) if r.get("phone")), None)
            if latest_with_name:
                user_profile["name"] = latest_with_name.get("name")
            if latest_with_phone:
                user_profile["phone"] = latest_with_phone.get("phone")
    except Exception as e:
        logger.error(f"[COLLECTOR] Failed to fetch user profile: {e}")
    
    # Enhanced extraction prompt with context
    extraction_prompt = f"""
    你是訂位資訊提取專家。從用戶輸入中提取訂位資訊。
    
    【重要規則】
    1. 當前日期: {current_date}
    2. 如果用戶說「明天」，計算正確的日期
    3. 如果用戶說「下午2點」，轉換為 24 小時制 (14:00)
    4. 如果某個欄位沒有提到，回傳 null
    
    【已知用戶資訊】（如果用戶沒提供，可使用這些）
    - 姓名: {user_profile.get('name', 'null')}
    - 電話: {user_profile.get('phone', 'null')}
    
    【用戶輸入】
    {user_input}
    
    請以 JSON 格式回傳:
    {{
        "date": "YYYY-MM-DD 或 null",
        "time": "HH:MM (24小時制) 或 null",
        "pax": 數字 或 null,
        "name": "姓名 或 null",
        "phone": "電話 或 null",
        "floor": 數字(1/2/3) 或 null
    }}
    
    範例:
    - 輸入: "明天下午2點，4個人" → {{"date": "2026-02-05", "time": "14:00", "pax": 4, "name": null, "phone": null, "floor": null}}
    - 輸入: "2026-02-10 18:00，6個人，我叫陳小明" → {{"date": "2026-02-10", "time": "18:00", "pax": 6, "name": "陳小明", "phone": null, "floor": null}}
    """
    
    try:
        response = await llm.ainvoke([HumanMessage(content=extraction_prompt)])
        # Parse JSON from response
        import json
        import re
        
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response.content, re.DOTALL)
        if json_match:
            extracted_data = json.loads(json_match.group(1))
        else:
            extracted_data = json.loads(response.content)
        
        # Update booking slot with extracted data
        for key, value in extracted_data.items():
            if value is not None and value != "null":
                state["booking_slot"][key] = value
        
        # If name/phone not in input but in profile, use profile
        if not state["booking_slot"].get("name") and user_profile.get("name"):
            state["booking_slot"]["name"] = user_profile["name"]
        if not state["booking_slot"].get("phone") and user_profile.get("phone"):
            state["booking_slot"]["phone"] = user_profile["phone"]
        
        logger.info(f"[COLLECTOR] Updated booking slot: {state['booking_slot']}")
        
    except Exception as e:
        logger.error(f"[COLLECTOR] Extraction failed: {e}")
    
    return state



async def business_rules_node(state: AgentState) -> AgentState:
    """
    Pure Python validation - NO LLM.
    This ensures 100% accuracy for business logic.
    """
    logger.info("[RULES] Validating business rules...")
    
    booking = state["booking_slot"]
    
    # Check if date is in the past
    if booking.get("date"):
        current_date = datetime.now().strftime("%Y-%m-%d")
        if booking["date"] < current_date:
            state["error_msg"] = "日期不能是過去的時間"
            state["next_step"] = "ask_correction"
            return state
    
    # Check business hours
    if booking.get("date") and booking.get("time"):
        try:
            dt = datetime.strptime(booking["date"], "%Y-%m-%d")
            day_name = dt.strftime("%A")
            
            hours = await db.get_business_hours()
            day_config = hours.get(day_name)
            
            if not day_config or day_config.get("closed"):
                state["error_msg"] = f"抱歉，我們在 {day_name} 休息"
                state["next_step"] = "ask_correction"
                return state
            
            open_time = day_config.get("open", "09:00")
            close_time = day_config.get("close", "18:00")
            
            if not (open_time <= booking["time"] <= close_time):
                state["error_msg"] = f"營業時間為 {open_time} - {close_time}"
                state["next_step"] = "ask_correction"
                return state
                
        except ValueError:
            state["error_msg"] = "日期格式錯誤"
            state["next_step"] = "ask_correction"
            return state
    
    # Check availability
    if booking.get("date") and booking.get("time") and booking.get("pax"):
        is_available = await db.check_availability(
            booking["date"],
            booking["time"],
            booking["pax"]
        )
        
        if not is_available:
            state["error_msg"] = "該時段已客滿"
            state["next_step"] = "ask_correction"
            return state
    
    # All checks passed
    state["error_msg"] = ""
    state["next_step"] = "execute"
    logger.info("[RULES] All validations passed")
    
    return state


async def execute_booking_node(state: AgentState) -> AgentState:
    """
    Execute the actual booking in the database.
    """
    logger.info("[EXECUTE] Creating reservation...")
    
    booking = state["booking_slot"]
    
    try:
        from src.agents_legacy import ReservationQueryAgent
        agent = ReservationQueryAgent()
        
        result = await agent.book_table(
            date=booking["date"],
            time=booking["time"],
            pax=booking["pax"],
            name=booking["name"],
            phone=booking["phone"],
            floor=booking.get("floor"),
            allow_split=booking.get("allow_split", False),
            context={"user_id": state["user_id"]}
        )
        
        state["final_response"] = result
        logger.info("[EXECUTE] Booking successful")
        
    except Exception as e:
        logger.error(f"[EXECUTE] Booking failed: {e}")
        state["final_response"] = "抱歉，訂位時發生錯誤，請稍後再試。"
    
    return state


async def response_generator_node(state: AgentState) -> AgentState:
    """
    Generate natural language response based on current state.
    This node has STRICT language control and rich context.
    """
    logger.info("[RESPONDER] Generating response...")
    
    # If final_response is already set (e.g., from execute_booking), return it
    if state.get("final_response"):
        return state
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY")
    )
    
    # Fetch menu and business info for context
    menu_str = ""
    hours_str = ""
    try:
        menu_items = await db.get_menu()
        menu_str = "\n".join([f"- {item['name']} (${item['price']}): {item.get('description', '')}" 
                              for item in menu_items[:10]])  # Limit to 10 items
        
        hours_config = await db.get_business_hours()
        hours_list = []
        for day, config in hours_config.items():
            if config.get("closed"):
                hours_list.append(f"- {day}: 休息")
            else:
                hours_list.append(f"- {day}: {config.get('open')} - {config.get('close')}")
        hours_str = "\n".join(hours_list)
    except Exception as e:
        logger.error(f"[RESPONDER] Failed to fetch context: {e}")
    
    # Determine what to say based on state
    missing_fields = get_missing_booking_fields(state["booking_slot"])

    if state.get("intent") == "unclear":
        # Intent was unclear - ask user what they want to do
        task = """用戶的意圖不明確。請友善地詢問用戶想做什麼，並提供選項：

        1. 訂位
        2. 查看菜單
        3. 修改/取消訂位
        4. 其他問題

        用簡短親切的方式詢問，不要列出編號，用自然的語句。"""
    elif state.get("error_msg"):
        # There's an error to communicate
        task = f"告訴用戶：{state['error_msg']}，並請他們提供正確的資訊。"
    elif state.get("intent") == "booking" and missing_fields:
        # Need to ask for missing information
        field_names = {
            "date": "日期",
            "time": "時間",
            "pax": "人數",
            "name": "大名",
            "phone": "電話"
        }
        missing_str = "、".join([field_names.get(f, f) for f in missing_fields])
        task = f"請禮貌地詢問用戶的 {missing_str}。一次只問一個問題。"
    else:
        # General response - get user's last message
        user_msg = state["messages"][-1].content if state["messages"] else "你好"
        task = f"用戶說：「{user_msg}」。請友善地回應。"
    
    # STRICT language enforcement
    lang_instruction = "你必須使用繁體中文回應。" if state["language"] == "zh-TW" else "You must respond in English."
    
    # Rich context prompt
    prompt = f"""
    {lang_instruction}
    
    你是「言文字」咖啡廳的訂位助理。
    
    【咖啡廳資訊】
    
    營業時間:
    {hours_str}
    
    樓層指南:
    - 1樓: 限時 90 分鐘
    - 2樓: 不限時，適合聊天
    - 3樓: 不限時，安靜區（禁止聊天）
    
    熱門餐點:
    {menu_str}
    
    【你的任務】
    {task}
    
    【回應規則】
    1. 簡潔有禮，不要囉嗦
    2. 不要重複已知資訊
    3. 一次只問一個問題
    4. 如果用戶說「沒有了」、「就這樣」，禮貌結束對話
    5. 保持專業但友善的語氣
    """
    
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        state["final_response"] = response.content
        
        # Post-processing: Force language check
        if state["language"] == "zh-TW":
            # If response contains mostly English, re-translate
            if detect_language(response.content) == "en":
                logger.warning("[RESPONDER] Detected English response, forcing re-generation...")
                retry_prompt = f"請將以下內容翻譯成繁體中文，保持禮貌和專業：\n{response.content}"
                retry_response = await llm.ainvoke([HumanMessage(content=retry_prompt)])
                state["final_response"] = retry_response.content
        
    except Exception as e:
        logger.error(f"[RESPONDER] Response generation failed: {e}")
        state["final_response"] = "抱歉，我遇到了一些問題。" if state["language"] == "zh-TW" else "Sorry, I encountered an issue."
    
    return state



# ============================================================================
# GRAPH CONSTRUCTION
# ============================================================================

def create_booking_graph():
    """
    Build the LangGraph state machine.
    """
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("router", router_node)
    workflow.add_node("booking_collector", booking_collector_node)
    workflow.add_node("business_rules", business_rules_node)
    workflow.add_node("execute_booking", execute_booking_node)
    workflow.add_node("responder", response_generator_node)
    
    # Define edges
    workflow.set_entry_point("router")
    
    # Router decides next step
    def route_after_router(state: AgentState) -> str:
        if state["intent"] == "booking":
            return "booking_collector"
        else:
            return "responder"
    
    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "booking_collector": "booking_collector",
            "responder": "responder"
        }
    )
    
    # After collecting booking info, validate
    workflow.add_edge("booking_collector", "business_rules")
    
    # After validation, decide next step
    def route_after_validation(state: AgentState) -> str:
        if state["next_step"] == "execute":
            missing = get_missing_booking_fields(state["booking_slot"])
            if not missing:
                return "execute_booking"
        return "responder"
    
    workflow.add_conditional_edges(
        "business_rules",
        route_after_validation,
        {
            "execute_booking": "execute_booking",
            "responder": "responder"
        }
    )
    
    # After execution, generate final response
    workflow.add_edge("execute_booking", "responder")
    
    # Responder is the end
    workflow.add_edge("responder", END)
    
    return workflow.compile()


# ============================================================================
# MAIN INTERFACE
# ============================================================================

class LangGraphAgent:
    """
    Main agent interface that replaces ConversationAgent.
    """
    def __init__(self):
        self.graph = create_booking_graph()
        logger.info("[LANGGRAPH] Agent initialized")
    
    async def process(self, input_text: str, context: Dict[str, Any] = None) -> str:
        """
        Process user input through the LangGraph state machine.
        """
        user_id = context.get("user_id", "unknown_user") if context else "unknown_user"
        
        # Initialize state
        initial_state: AgentState = {
            "messages": [HumanMessage(content=input_text)],
            "user_id": user_id,
            "user_profile": {},
            "intent": "unknown",
            "booking_slot": {},
            "next_step": "",
            "error_msg": "",
            "language": detect_language(input_text),
            "final_response": ""
        }
        
        # Run the graph
        try:
            final_state = await self.graph.ainvoke(initial_state)
            return final_state["final_response"]
        except Exception as e:
            logger.error(f"[LANGGRAPH] Graph execution failed: {e}")
            return "抱歉，系統發生錯誤。" if initial_state["language"] == "zh-TW" else "Sorry, system error."


# Singleton instance
langgraph_agent = LangGraphAgent()
