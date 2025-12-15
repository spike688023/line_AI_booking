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
from linebot.models import MessageEvent, TextMessage, TextSendMessage
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

# Admin Configuration
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123") # Default password

# Initialize Line Bot
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    logger.warning("Line Channel Secret or Access Token not set. Webhook will not work.")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN) if CHANNEL_ACCESS_TOKEN else None
parser = WebhookParser(CHANNEL_SECRET) if CHANNEL_SECRET else None

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
    except InvalidSignatureError:
        logger.error("Invalid signature. Please check your channel access token/channel secret.")
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

async def handle_message_async(event):
    user_id = event.source.user_id
    user_message = event.message.text
    
    logger.info(f"Received message from {user_id}: {user_message}")
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
