import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from src.database import db

# Initialize Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI()

# Initialize Templates
templates = Jinja2Templates(directory="templates")

# Mount Static Files
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")

from datetime import datetime

@app.get("/seating-map", response_class=HTMLResponse)
async def seating_map(request: Request, date: str = None):
    # Default to today if not provided
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
        
    occupied_tables = await db.get_daily_occupied_tables(date)
    
    # Pre-process seat assignments for the template
    for tid, data in occupied_tables.items():
        seat_map = []
        for b in data.get("bookings", []):
            for _ in range(b.get("pax", 0)):
                seat_map.append(b.get("res_id", ""))
        data["seat_map"] = seat_map
    
    return templates.TemplateResponse("seating_map.html", {
        "request": request,
        "date": date,
        "occupied_tables": occupied_tables
    })

# Admin Configuration
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123") # Default password

# Initialize Line Bot
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    logger.warning("Line Channel Secret or Access Token not set. Webhook will not work.")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN) if CHANNEL_ACCESS_TOKEN else None
parser = WebhookParser(CHANNEL_SECRET) if CHANNEL_SECRET else None

# Admin Line User IDs (For notifications)
# We now use DB for this, but keep env as fallback or initial seed
ENV_ADMIN_IDS = os.getenv("ADMIN_LINE_USER_ID", "")

async def get_admin_ids():
    """Helper to get admin IDs from DB, falling back to env."""
    settings = await db.get_notification_settings()
    db_ids = settings.get("admin_ids", [])
    
    # Also include env IDs if not empty
    env_ids = [aid.strip() for aid in ENV_ADMIN_IDS.split(",") if aid.strip()]
    
    # Combine unique IDs
    return list(set(db_ids + env_ids))

async def send_admin_notification(message: str):
    """Send a push message to all admins."""
    if not line_bot_api:
        logger.warning("Cannot send admin notification: Line Bot API not initialized.")
        return
    
    admin_ids = await get_admin_ids()
    
    if not admin_ids:
        logger.warning("Cannot send admin notification: No admin IDs found.")
        return

    try:
        # Multicast is more efficient for sending to multiple users
        line_bot_api.multicast(
            admin_ids,
            TextSendMessage(text=f"🔔 [New Notification]\n{message}")
        )
        logger.info(f"Admin notification sent to {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"Failed to send admin notification: {e}")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Coffee Shop Agent is running"}

# --- Admin Routes ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login_post(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        # In a real app, use session/cookies. For simplicity, we just redirect to dashboard
        # But wait, without cookies, we can't protect /dashboard. 
        # Let's use a simple query param hack or just render dashboard directly for this MVP.
        # Better: Set a cookie.
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_session", value="logged_in")
        return response
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid Password"})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, include_past: bool = False):
    # Simple cookie check
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    reservations = await db.get_all_reservations(include_past=include_past)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "reservations": reservations,
        "include_past": include_past
    })

@app.post("/admin/cleanup")
async def cleanup_reservations(request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    count = await db.delete_past_reservations()
    # Redirect back to dashboard, maybe with a flash message (not implemented here)
    return RedirectResponse(url="/admin/dashboard", status_code=303)

@app.post("/admin/delete/{reservation_id}")
async def delete_reservation(reservation_id: str, request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    await db.delete_reservation(reservation_id)
    return RedirectResponse(url="/admin/dashboard", status_code=303)

@app.post("/admin/update/{reservation_id}")
async def update_reservation(reservation_id: str, request: Request, new_date: str = Form(...), new_time: str = Form(...)):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    # Admin update bypasses user_id check
    await db.modify_reservation(reservation_id, new_date, new_time, user_id="admin", is_admin=True)
    return RedirectResponse(url="/admin/dashboard", status_code=303)

# --- Menu Management Routes ---

@app.get("/admin/menu", response_class=HTMLResponse)
async def menu_dashboard(request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    menu_items = await db.get_menu()
    return templates.TemplateResponse("menu_dashboard.html", {
        "request": request,
        "menu_items": menu_items
    })

@app.post("/admin/menu/add")
async def add_menu_item(request: Request, name: str = Form(...), price: int = Form(...), category: str = Form(...), description: str = Form("")):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    await db.add_menu_item(name, price, category, description)
    return RedirectResponse(url="/admin/menu", status_code=303)

@app.post("/admin/menu/update/{item_id}")
async def update_menu_item(item_id: str, request: Request, name: str = Form(...), price: int = Form(...), category: str = Form(...), description: str = Form("")):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    data = {
        "name": name,
        "price": price,
        "category": category,
        "description": description
    }
    await db.update_menu_item(item_id, data)
    return RedirectResponse(url="/admin/menu", status_code=303)

@app.post("/admin/menu/delete/{item_id}")
async def delete_menu_item(item_id: str, request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    await db.delete_menu_item(item_id)
    return RedirectResponse(url="/admin/menu", status_code=303)

# --- Business Hours Routes ---

@app.get("/admin/hours", response_class=HTMLResponse)
async def hours_dashboard(request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    hours = await db.get_business_hours()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    special_closures = await db.get_special_closures()
    
    return templates.TemplateResponse("hours_dashboard.html", {
        "request": request,
        "hours": hours,
        "days": days,
        "special_closures": sorted(special_closures)
    })

@app.post("/admin/hours/update")
async def update_hours(request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    form_data = await request.form()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    new_hours = {}
    
    for day in days:
        is_closed = form_data.get(f"{day}_closed") == "on"
        new_hours[day] = {
            "open": form_data.get(f"{day}_open"),
            "close": form_data.get(f"{day}_close"),
            "closed": is_closed
        }
    
    await db.update_business_hours(new_hours)
    return RedirectResponse(url="/admin/hours", status_code=303)

@app.post("/admin/closures/add")
async def add_closure(request: Request, date: str = Form(...)):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    await db.add_special_closure(date)
    return RedirectResponse(url="/admin/hours", status_code=303)

@app.post("/admin/closures/remove")
async def remove_closure(request: Request, date: str = Form(...)):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    await db.remove_special_closure(date)
    return RedirectResponse(url="/admin/hours", status_code=303)

# --- Notification Settings Routes ---

@app.get("/admin/notifications", response_class=HTMLResponse)
async def notifications_dashboard(request: Request):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    
    return templates.TemplateResponse("notifications_dashboard.html", {
        "request": request,
        "admin_ids": admin_ids
    })

@app.post("/admin/notifications/add")
async def add_notification_id(request: Request, user_id: str = Form(...)):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    
    if user_id and user_id not in admin_ids:
        admin_ids.append(user_id.strip())
        await db.update_notification_settings(admin_ids)
    
    return RedirectResponse(url="/admin/notifications", status_code=303)

@app.post("/admin/notifications/remove")
async def remove_notification_id(request: Request, user_id: str = Form(...)):
    if request.cookies.get("admin_session") != "logged_in":
        return RedirectResponse(url="/admin")
    
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    
    if user_id in admin_ids:
        admin_ids.remove(user_id)
        await db.update_notification_settings(admin_ids)
    
    return RedirectResponse(url="/admin/notifications", status_code=303)

# --- Webhook Routes ---

@app.post("/callback")
async def callback(request: Request):
    # Get X-Line-Signature header value
    signature = request.headers.get("X-Line-Signature", "")

    # Get request body as text
    body = await request.body()
    body_text = body.decode("utf-8")

    logger.info("Request body: " + body_text)

    # Handle webhook body
    try:
        if parser:
            events = parser.parse(body_text, signature)
            for event in events:
                if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
                    await handle_message_async(event)
                elif isinstance(event, FollowEvent):
                    await handle_follow_async(event)
    except InvalidSignatureError:
        logger.error("Invalid signature. Please check your channel access token/channel secret.")
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

async def handle_message_async(event):
    user_id = event.source.user_id
    user_message = event.message.text
    
    logger.info(f"Received message from {user_id}: {user_message}")

    # Helper command to get User ID
    if user_message.strip().lower() == "id":
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"Your User ID is:\n{user_id}")
            )
        except Exception as e:
            logger.error(f"Error sending reply: {e}")
        return
    
    # Integrate with Conversation Agent
    from src.agents import conversation_agent

    try:
        # Await the async process directly in the main loop
        response_text = await conversation_agent.process(user_message, context={"user_id": user_id})
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        response_text = "Sorry, I encountered an error processing your request."
    
    # Reply using sync API (blocking, but acceptable for now)
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response_text)
        )
    except Exception as e:
        logger.error(f"Error sending reply: {e}")

async def handle_follow_async(event):
    welcome_text = (
        "您好！我是「言文字」AI 訂位系統 ☕️\n\n"
        "很高興能為您服務！我可以幫您：\n"
        "1. 預約訂位 📅\n"
        "2. 查詢或修改您的訂位內容\n\n"
        "請問今天有什麼我可以幫您的嗎？"
    )
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=welcome_text)
        )
    except Exception as e:
        logger.error(f"Error sending welcome reply: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
