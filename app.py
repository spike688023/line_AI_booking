import os
import hmac
import hashlib
import base64
import json
import logging
from urllib.parse import urlencode
from dotenv import load_dotenv
from collections import deque

import httpx
import jwt as pyjwt

# Load environment variables
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from src.database import db
from src.agents_graph import langgraph_agent

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

# Simple In-Memory De-duplication for Webhooks
processed_events = deque(maxlen=2000)

# Initialize Templates
templates = Jinja2Templates(directory="templates")

# Mount Static Files
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- JWT + Google OAuth config ---

JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_in_prod")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
_REDIRECT_BASE = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://localhost:8080")
GOOGLE_REDIRECT_URI = f"{_REDIRECT_BASE}/auth/google/callback"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def _exchange_google_code_for_email(code: str) -> str:
    """Exchange an OAuth authorization code for the user's Google email."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        id_token_str = resp.json()["id_token"]
    claims = pyjwt.decode(id_token_str, options={"verify_signature": False})
    return claims["email"]


def _create_admin_jwt(store_id: str, email: str) -> str:
    return pyjwt.encode({"store_id": store_id, "email": email}, JWT_SECRET, algorithm="HS256")


def _verify_admin_jwt(token: str) -> dict:
    return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])


async def get_current_store(request: Request) -> str:
    """FastAPI dependency: validates admin_token cookie, returns store_id."""
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=307, headers={"Location": "/admin"})
    try:
        payload = _verify_admin_jwt(token)
        return payload["store_id"]
    except Exception:
        raise HTTPException(status_code=307, headers={"Location": "/admin"})


async def send_admin_notification(message: str):
    """Send a push message to all admins. Requires a line_bot_api instance — wired in T7."""
    logger.warning("send_admin_notification: no per-store line_bot_api yet (wired in T7)")
    return

@app.get("/")
async def root():
    return {"status": "ok", "message": "Coffee Shop Agent is running"}

# --- Admin Routes ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_login(request: Request):
    auth_url = (
        f"{GOOGLE_AUTH_URL}?response_type=code"
        f"&client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&scope=openid%20email"
        f"&access_type=offline"
    )
    return templates.TemplateResponse("login.html", {"request": request, "auth_url": auth_url})


@app.get("/auth/google/callback")
async def google_oauth_callback(code: str = None):
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    try:
        email = await _exchange_google_code_for_email(code)
    except Exception as e:
        logger.error(f"Google token exchange failed: {e}")
        raise HTTPException(status_code=400, detail="Failed to exchange code")
    store = await db.get_store_by_admin_email(email)
    if store is None:
        raise HTTPException(status_code=403, detail="Email not authorized for any store")
    token = _create_admin_jwt(store["store_id"], email)
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(key="admin_token", value=token, httponly=True)
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, include_past: bool = False, store_id: str = Depends(get_current_store)):
    reservations = await db.get_all_reservations(include_past=include_past)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "reservations": reservations,
        "include_past": include_past,
    })

@app.post("/admin/cleanup")
async def cleanup_reservations(store_id: str = Depends(get_current_store)):
    await db.delete_past_reservations()
    return RedirectResponse(url="/admin/dashboard", status_code=303)

@app.post("/admin/delete/{reservation_id}")
async def delete_reservation(reservation_id: str, store_id: str = Depends(get_current_store)):
    await db.delete_reservation(reservation_id)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


# --- Menu Management Routes ---

@app.get("/admin/menu", response_class=HTMLResponse)
async def menu_dashboard(request: Request, store_id: str = Depends(get_current_store)):
    menu_items = await db.get_menu()
    return templates.TemplateResponse("menu_dashboard.html", {
        "request": request,
        "menu_items": menu_items,
    })

@app.post("/admin/menu/add")
async def add_menu_item(name: str = Form(...), price: int = Form(...), category: str = Form(...), description: str = Form(""), store_id: str = Depends(get_current_store)):
    await db.add_menu_item(name, price, category, description)
    return RedirectResponse(url="/admin/menu", status_code=303)

@app.post("/admin/menu/update/{item_id}")
async def update_menu_item(item_id: str, name: str = Form(...), price: int = Form(...), category: str = Form(...), description: str = Form(""), store_id: str = Depends(get_current_store)):
    data = {"name": name, "price": price, "category": category, "description": description}
    await db.update_menu_item(item_id, data)
    return RedirectResponse(url="/admin/menu", status_code=303)

@app.post("/admin/menu/delete/{item_id}")
async def delete_menu_item(item_id: str, store_id: str = Depends(get_current_store)):
    await db.delete_menu_item(item_id)
    return RedirectResponse(url="/admin/menu", status_code=303)

# --- Business Hours Routes ---

@app.get("/admin/hours", response_class=HTMLResponse)
async def hours_dashboard(request: Request, store_id: str = Depends(get_current_store)):
    hours = await db.get_business_hours()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    special_closures = await db.get_special_closures()
    return templates.TemplateResponse("hours_dashboard.html", {
        "request": request,
        "hours": hours,
        "days": days,
        "special_closures": sorted(special_closures),
    })

@app.post("/admin/hours/update")
async def update_hours(request: Request, store_id: str = Depends(get_current_store)):
    form_data = await request.form()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    new_hours = {}
    for day in days:
        is_closed = form_data.get(f"{day}_closed") == "on"
        new_hours[day] = {
            "open": form_data.get(f"{day}_open"),
            "close": form_data.get(f"{day}_close"),
            "closed": is_closed,
        }
    await db.update_business_hours(new_hours)
    return RedirectResponse(url="/admin/hours", status_code=303)

@app.post("/admin/closures/add")
async def add_closure(date: str = Form(...), store_id: str = Depends(get_current_store)):
    await db.add_special_closure(date)
    return RedirectResponse(url="/admin/hours", status_code=303)

@app.post("/admin/closures/remove")
async def remove_closure(date: str = Form(...), store_id: str = Depends(get_current_store)):
    await db.remove_special_closure(date)
    return RedirectResponse(url="/admin/hours", status_code=303)

# --- Notification Settings Routes ---

@app.get("/admin/notifications", response_class=HTMLResponse)
async def notifications_dashboard(request: Request, store_id: str = Depends(get_current_store)):
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    return templates.TemplateResponse("notifications_dashboard.html", {
        "request": request,
        "admin_ids": admin_ids,
    })

@app.post("/admin/notifications/add")
async def add_notification_id(user_id: str = Form(...), store_id: str = Depends(get_current_store)):
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    if user_id and user_id not in admin_ids:
        admin_ids.append(user_id.strip())
        await db.update_notification_settings(admin_ids)
    return RedirectResponse(url="/admin/notifications", status_code=303)

@app.post("/admin/notifications/remove")
async def remove_notification_id(user_id: str = Form(...), store_id: str = Depends(get_current_store)):
    settings = await db.get_notification_settings()
    admin_ids = settings.get("admin_ids", [])
    if user_id in admin_ids:
        admin_ids.remove(user_id)
        await db.update_notification_settings(admin_ids)
    return RedirectResponse(url="/admin/notifications", status_code=303)

# --- Webhook Routes ---

def _verify_line_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    mac = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature)


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    logger.info("Request body: " + body.decode("utf-8"))

    # Step 1: extract destination from raw JSON (before signature check)
    try:
        payload = json.loads(body)
        destination = payload.get("destination", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Step 2: look up store by destination
    store = await db.get_store_by_destination(destination)
    if store is None:
        raise HTTPException(status_code=404, detail="Unknown destination")

    store_id = store["store_id"]

    # Step 3: fetch per-store credentials and verify HMAC
    channel_access_token, channel_secret = await db.get_store_credentials(store_id)
    if not _verify_line_signature(channel_secret, body, signature):
        logger.error(f"Invalid signature for store {store_id}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Step 4: construct per-request LINE clients
    per_request_api = LineBotApi(channel_access_token)
    per_request_parser = WebhookParser(channel_secret)

    events = per_request_parser.parse(body.decode("utf-8"), signature)
    for event in events:
        if hasattr(event, "webhook_event_id"):
            eid = event.webhook_event_id
            if eid in processed_events:
                logger.info(f"Duplicate event {eid} detected. Skipping.")
                continue
            processed_events.append(eid)

        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            await handle_message_async(event, per_request_api, store_id)
        elif isinstance(event, FollowEvent):
            await handle_follow_async(event, per_request_api)

    return "OK"


async def handle_message_async(event, line_bot_api: LineBotApi, store_id: str = ""):
    user_id = event.source.user_id
    user_message = event.message.text

    logger.info(f"Received message from {user_id}: {user_message}")

    if user_message.strip().lower() == "id":
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"Your User ID is:\n{user_id}")
            )
        except Exception as e:
            logger.error(f"Error sending reply: {e}")
        return

    try:
        response_text = await langgraph_agent.process(
            user_message, context={"user_id": user_id}, store_id=store_id
        )
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        response_text = "Sorry, I encountered an error processing your request."

    try:
        line_bot_api.push_message(user_id, TextSendMessage(text=response_text))
        logger.info(f"Sent push message to {user_id}")
    except Exception as e:
        logger.warning(f"Push message failed: {e}, trying reply_message")
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response_text))
            logger.info(f"Sent reply message to {user_id}")
        except Exception as e2:
            logger.error(f"Both push and reply failed: {e2}")


async def handle_follow_async(event, line_bot_api: LineBotApi):
    welcome_text = (
        "您好！我是「言文字」AI 訂位系統 ☕️\n\n"
        "很高興能為您服務！我可以幫您：\n"
        "1. 預約訂位 📅\n"
        "2. 查詢或修改您的訂位內容\n\n"
        "請問今天有什麼我可以幫您的嗎？"
    )
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))
    except Exception as e:
        logger.error(f"Error sending welcome reply: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
