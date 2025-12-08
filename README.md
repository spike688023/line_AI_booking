# ☕ Coffee Shop Reservation Agent

A Line Bot based reservation system powered by Google ADK and Google Cloud.

## 🏗️ Architecture

- **Frontend**: Line Mobile App
- **Backend**: FastAPI on Cloud Run
- **AI**: Google ADK (Multi-Agent System)
- **Database**: Firestore

## 🚀 Agents

1. **Conversation Agent**: Main orchestrator.
2. **Reservation Agent**: Checks table availability.
3. **Order Agent**: Creates reservation orders.
4. **Payment Agent**: Verifies payment status.

## 📂 Structure

- `src/`: Source code for agents and API handlers.
- `tests/`: Unit and integration tests.
- `app.py`: Main entry point for Cloud Run.

## 🛠️ Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure `.env`:
   ```
   LINE_CHANNEL_SECRET=...
   LINE_CHANNEL_ACCESS_TOKEN=...
   GOOGLE_API_KEY=...
   GOOGLE_CLOUD_PROJECT=...
   ```

3. Run locally:
   ```bash
   uvicorn app:app --reload
   ```
