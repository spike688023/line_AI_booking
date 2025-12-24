# ☕ Coffee Shop AI Reservation System

An intelligent, multi-agent reservation system for coffee shops, integrated with **LINE Bot** and featuring a real-time **Interactive Seating Map**. 

Powered by **Google Gemini AI**, **FastAPI**, and **Google Cloud Firestore**, deployed on **Cloud Run**.

---

## 🌟 Key Features

### 🤖 Intelligent AI Booking
*   **Multi-Agent Orchestration**: Uses a `ConversationAgent` to handle natural language via Gemini AI, coordinating with specialized agents for reservations, orders, and payments.
*   **Natural Language Support**: Full support for **Traditional Chinese** and English.
*   **Smart Validation**: Prevents past-date bookings and respects business hours and store policies.
*   **Function Calling**: AI can autonomously check availability, create/modify reservations, and show booking history.

### 🗺️ Interactive Seating Map
*   **Real-time Visualization**: Dynamic floor plans for 2nd and 3rd floors.
*   **Occupancy States**: Visual indicators for **Available**, **Partially Occupied**, and **Fully Occupied** tables.
*   **Booking Details**: Click any table to see a list of current reservations (Name, Pax, Time).
*   **Responsive Design**: Fully optimized for both Desktop and Mobile browsing.

### 🏢 Advanced Seating & Shared Tables
*   **Shared Table Support**: Multiple smaller parties can occupy a single large table to maximize capacity.
*   **Daily Allocation**: Optimized for all-day seating with a "Compactness" strategy to fill tables efficiently.
*   **Capacity Logic**: Detailed tracking of "Booked Pax" vs "Table Capacity".

### 📊 Admin Management Dashboard
*   **Reservations**: View, search, and modify all confirmed bookings.
*   **Menu Management**: Add, edit, or remove categories and items.
*   **Business Hours**: Flexible configuration for daily open/closed status.
*   **Notifications**: Manage admin LINE IDs for receiving real-time booking alerts.

---

## 🏗️ Architecture & Tech Stack

*   **Frontend**: LINE Mobile UI + Responsive HTML/Tailwind/Picnic (Admin Panels).
*   **Backend**: FastAPI (Python 3.12).
*   **AI Engine**: Google Gemini 2.0 Flash (via Google ADK pattern).
*   **Database**: Google Cloud Firestore (using Transactions for race-condition prevention).
*   **Deployment**: Google Cloud Run + Cloud Build.

---

## 📂 Project Structure

- `src/`: Core logic, Agents, and Database handlers.
- `templates/`: Jinja2 templates for Admin Dashboards and Seating Map.
- `tests/`: Comprehensive test suite (Logic, Database, API).
- `app.py`: FastAPI entry point and LINE Webhook handler.
- `deploy.sh`: Automated deployment script for Google Cloud.

---

## 🛠️ Setup & Local Development

### 1. Prerequisites
*   Python 3.10+
*   Google Cloud Project with Firestore enabled.
*   LINE Messaging API Channel.

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Environment Variables (`.env`)
```env
# LINE Messaging API
LINE_CHANNEL_SECRET=your_secret
LINE_CHANNEL_ACCESS_TOKEN=your_token

# Google AI
GOOGLE_API_KEY=your_gemini_key

# Google Cloud
GOOGLE_CLOUD_PROJECT=your_project_id
FIRESTORE_DATABASE=(default) # Optional
```

### 4. Run Locally
```bash
uvicorn app:app --reload --port 8080
```

---

## 🚀 Deployment

The project is configured for **Google Cloud Build** and **Cloud Run**.

```bash
# Ensure you have gcloud CLI installed and authenticated
chmod +x deploy.sh
./deploy.sh
```

---

## 🧪 Testing

Run the automated test suite to verify seating logic and agent behavior:

```bash
PYTHONPATH=. pytest tests/
```
