import os
import logging
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
