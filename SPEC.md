# SPEC: LINE Marketplace 多租戶 SaaS 架構

---

## ⚡ 架構變更（2026-06-14）：簡化 Onboarding 流程

### 變更動機

原設計使用 LINE OAuth onboarding 讓系統自動取得店家 LINE 憑證，但此方式需要成為 LINE Partner 才能正式上架。現改為更簡單的手動填入方式，任何人都能立即使用。

### 變更後的店家流程

```
原來：LINE OAuth → 系統自動拿憑證 → 填資料 → Google 綁定
現在：Google 登入（自動建帳號）→ /admin/line-settings 手動貼憑證 → 完成
```

### 需要修改的範圍

**刪除：**
- `GET /onboarding/start` — LINE OAuth 入口
- `GET /auth/line/callback` — LINE OAuth callback
- `GET /onboarding` — onboarding 表單頁
- `POST /onboarding/complete` — 提交 onboarding 資料
- `_exchange_line_code_for_credentials()` function
- `LINE_CLIENT_ID`, `LINE_CLIENT_SECRET_FOR_OAUTH` env vars（可保留供 webhook 驗簽用）

**修改：**
- `GET /auth/google/callback`：第一次登入時自動建立 store（`store_id = email hash`），不再要求事先 onboarding
- `GET /admin`：移除 `line_onboarding_store_id` cookie 邏輯

**新增：**
- `GET /admin/line-settings` — 顯示 LINE 憑證設定頁（Channel Access Token + Channel Secret）
- `POST /admin/line-settings` — 儲存憑證到 Firestore，並自動呼叫 LINE API 設定 webhook URL

### store_id 產生方式變更

| | 原來 | 新的 |
|---|---|---|
| 產生時機 | LINE OAuth callback | Google 首次登入 |
| 值 | LINE Bot ID (`destination`) | `hashlib.md5(email.encode()).hexdigest()[:12]` |
| 優點 | 和 webhook 原生對應 | 不需 LINE OAuth |
| 缺點 | 需要 LINE Partner | webhook 需用 channel secret 查 store_id |

### Webhook 識別方式變更

原來用 `destination`（LINE Bot ID）直接對應 `store_id`。
新方式：Firestore 建立 `line_bot_id → store_id` 反查表，webhook 進來時用 `destination` 查出 `store_id`。

```python
# stores/{store_id} 新增欄位：
line_bot_id: string   # 從 LINE API /v2/bot/info 取得，存入後用於反查
```

### 新增的 `/admin/line-settings` 頁面內容

```
Channel Access Token: [________________]
Channel Secret:       [________________]
Webhook URL（唯讀）:  https://wellness-booking-.../callback/{store_id}

[儲存並設定 Webhook]
```

按下儲存後，後端：
1. 驗證 token 可呼叫 LINE API（`GET /v2/bot/info`）
2. 取得 `line_bot_id`，寫入 Firestore
3. 呼叫 `PUT https://api.line.me/v2/bot/channel/webhook/endpoint` 自動設 webhook URL
4. 建立 `line_bot_id → store_id` 反查記錄

### 驗收標準

- [ ] 店家用 Google 登入，第一次登入自動建立 store，無需任何 onboarding 頁面
- [ ] `/admin/line-settings` 頁面可輸入 Channel Access Token + Channel Secret
- [ ] 儲存後系統自動驗證憑證並設定 webhook URL
- [ ] `/callback` webhook 收到訊息時能正確找到對應 store
- [ ] 舊的 `/onboarding*` 和 `/auth/line/callback` 路由全部移除

---

## 目標

將 `coffee_shop_agent` 從單店應用重構為多租戶 SaaS，使其能上架 LINE 擴充套件市集。每家店的資料、AI Prompt、桌位配置互相完全隔離；每位老闆透過 Google SSO 自助完成帳號綁定，無需人工介入。

**目標使用者**：餐廳/咖啡廳老闆（管理者）、LINE 一般用戶（訂位者）

---

## 核心架構決策

| 決策點 | 選擇 | 原因 |
|---|---|---|
| 租戶識別 | `destination` (LINE Bot ID) 作為 `store_id` | Webhook payload 原生帶有，不需額外欄位 |
| 資料隔離 | Firestore sub-collection `stores/{store_id}/...` | NoSQL 天然支援，免 Schema 遷移 |
| 管理員認證 | Google SSO OAuth 2.0 | 取代現有明文密碼 cookie，支援多帳號 |
| Token 儲存 | Google Secret Manager（Firestore 只存 resource name） | `channel_secret` / `channel_access_token` 為敏感憑證 |
| Admin 路由 | Path-based (`/admin`)，單一網域 | 不需為每店設立子域名 |

---

## 1. 資料模型 (Firestore)

### 新增 `stores` collection

```
stores/{store_id}
  ├── name: string
  ├── line_bot_id: string          # 即 destination，也是 store_id
  ├── channel_access_token_ref: string  # Secret Manager resource name
  ├── channel_secret_ref: string        # Secret Manager resource name
  ├── admin_emails: string[]
  ├── custom_prompt: string        # 店家自訂 AI 語氣與規定
  └── config/
       ├── business_hours: { Monday: {open, close, closed}, ... }
       ├── special_closures: { dates: string[] }
       ├── notifications: { admin_ids: string[] }
       └── table_layout: { tables: { [table_id]: {capacity, floor} }, total_capacity: int }
```

### 現有 collections 的遷移

所有 collections 需加上 `store_id` 隔離，完整清單：

| Collection | 現況 (問題) | 修正後 |
|---|---|---|
| `reservations` | 無 `store_id` | 新增 `store_id` 欄位 |
| `orders` | 無 `store_id` | 新增 `store_id` 欄位 |
| `menu` | 無 `store_id` | 移至 `stores/{store_id}/menu/` sub-collection |
| `daily_slots` | `date` 為 doc ID（全域） | doc ID 改為 `{store_id}_{date}` |
| `conversation_states` | `user_id` 為 doc ID（全域） | doc ID 改為 `{store_id}_{user_id}` |
| `config/business_hours` | 全域單一文件 | 移至 `stores/{store_id}/config/business_hours` |
| `config/special_closures` | 全域單一文件 | 移至 `stores/{store_id}/config/special_closures` |
| `config/notifications` | 全域單一文件 | 移至 `stores/{store_id}/config/notifications` |

---

## 2. `src/database.py` 修改

### 2a. 移除硬碼桌位設定

目前 [database.py:33-53](src/database.py#L33-L53) 的 `TABLE_CONFIG` 與 `TOTAL_CAPACITY` 是寫死的單店資料，必須移出。

- 刪除 `TABLE_CONFIG` 與 `TOTAL_CAPACITY` class attributes
- 新增 `get_table_config(store_id: str) -> Dict`，從 `stores/{store_id}/config/table_layout` 讀取
- 下列函式改為接收 `store_id` 並動態載入桌位設定：
  - `create_reservation()`
  - `check_availability()`
  - `get_available_floors()`
  - `get_daily_occupied_tables()`

### 2b. 所有函式強制加上 `store_id` 參數

```python
# Before
async def get_menu(self) -> List[Dict]
async def get_all_reservations(self, include_past=False) -> List[Dict]
async def get_business_hours(self) -> Dict
async def get_conversation_state(self, user_id: str) -> Dict

# After
async def get_menu(self, store_id: str) -> List[Dict]
async def get_all_reservations(self, store_id: str, include_past=False) -> List[Dict]
async def get_business_hours(self, store_id: str) -> Dict
async def get_conversation_state(self, store_id: str, user_id: str) -> Dict
```

### 2c. 新增函式

```python
async def get_store_by_destination(destination: str) -> Optional[Dict]
# 用 line_bot_id == destination 查詢 stores collection，回傳店家資料（含 secret resource name）
# 這是 webhook 多租戶路由的入口點

async def get_store_credentials(store_id: str) -> Tuple[str, str]
# 從 Google Secret Manager 取出 (channel_access_token, channel_secret)

async def get_table_config(store_id: str) -> Dict
# 從 stores/{store_id}/config/table_layout 讀取桌位設定
```

---

## 3. `app.py` 修改

### 3a. 移除全域 Token 初始化

刪除 [app.py:86-94](app.py#L86-L94)：
```python
# 刪除這幾行
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN) ...
parser = WebhookParser(CHANNEL_SECRET) ...
```

### 3b. 改寫 `/callback` Webhook — 解決簽名驗證的雞蛋問題

LINE SDK 的 `WebhookParser.parse(body, signature)` 同時做 HMAC 驗證與 JSON 解析。多租戶場景下需先取得 `destination` 才能查到對應的 `channel_secret`，但 `destination` 在解析前無法取得。必須拆成手動兩步：

```python
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    # Step 1: 不驗簽，先取 destination（租戶識別）
    raw_payload = json.loads(body)
    destination = raw_payload.get("destination")
    if not destination:
        raise HTTPException(status_code=400, detail="Missing destination")

    # Step 2: 查詢該店憑證
    store = await db.get_store_by_destination(destination)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    channel_secret, channel_access_token = await db.get_store_credentials(store["store_id"])

    # Step 3: 手動 HMAC-SHA256 驗簽
    expected_sig = base64.b64encode(
        hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected_sig, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Step 4: 用該店 token 解析並處理 events
    store_id = store["store_id"]
    line_bot_api = LineBotApi(channel_access_token)
    events = WebhookParser(channel_secret).parse(body.decode(), signature)
    for event in events:
        await handle_event(event, store_id, line_bot_api)
```

### 3c. 新增 LINE 市集授權端點

```
GET  /auth/line/callback   # 接收市集安裝後的 OAuth 授權碼
                            # 換取 Channel Access Token → 寫入 Secret Manager
                            # 建立 stores document → 重導向到 /onboarding
```

### 3d. 新增店家 Onboarding 頁面

```
GET  /onboarding            # 店家填寫基本資料（需已完成 LINE 授權）
POST /onboarding/complete   # 提交店名、營業時間、桌位配置初始值
```

老闆在此使用 **Google 帳號登入**綁定信箱（寫入 `admin_emails`），完成後跳轉至 `/admin/dashboard`。

### 3e. 改寫 Admin 認證機制

取代現有的 `admin_session=logged_in` 明文 cookie。

```
GET  /admin                 # 顯示 Google 登入按鈕
GET  /auth/google/callback  # Google OAuth callback，查 admin_emails 取得 store_id
                            # 發行含 store_id 的 signed JWT，存入 HttpOnly cookie
GET  /admin/dashboard       # 驗證 JWT，依 store_id 過濾資料
```

若一個 Google 帳號對應多間店鋪，先顯示選擇畫面再跳轉。

---

## 4. `src/agents_graph.py` + `src/tools.py` 重構

> **三大核心目標**：速度、安全、體驗。

### 目標一：架構升級 — 從「接力賽」改為「大腦＋工具箱」

**現況問題**：`agents_graph.py` 是多節點串接的接力架構：
```
Intent 節點 → 等待 LLM → Extract 節點 → 等待 LLM → Generate 節點 → 等待 LLM
```
每次對話觸發 2-3 次 LLM 呼叫，回應時間約 **8 秒**。

**目標架構**：單一 Agent + Function Calling (`bind_tools`)
```
單一 LLM 思考一次 → 決定「純聊天」或「呼叫工具」→ 回覆
```
LLM 自行決定是否呼叫工具，無需人工節點分流，目標回應時間壓縮至 **3 秒內**。

**實作方式**：
- 刪除現有 `intent_node`、`extract_node` 等中間節點
- 改用 `llm.bind_tools([check_availability, execute_booking, ...])`
- LangGraph 圖結構簡化為：`agent_node → tools_node → agent_node`（ReAct loop）

### 目標二：安全防護 — 商業邏輯與 AI 完全分離

**原則**：LLM 只負責「理解語意、決定呼叫什麼工具」，不負責「判斷結果」。

| 層級 | 負責範圍 |
|---|---|
| LLM | 聽懂客人說的日期、人數、需求；決定呼叫哪個工具 |
| Python Tool | 查詢 DB、判斷是否有空位、執行寫入、回傳結構化結果 |

**效果**：徹底杜絕幻覺（例如客滿卻回覆訂位成功）。LLM 拿到的是 Python 驗證過的真實資料，再用自然語言包裝給客人。

**工具函式規範**（`src/tools.py`）：
```python
# 所有工具必須接收 store_id，在 DB 層做隔離
async def check_availability(store_id: str, date: str, time: str, pax: int) -> dict
async def execute_booking(store_id: str, user_id: str, date: str, time: str, pax: int, name: str, phone: str) -> dict
# 回傳 {"success": bool, "data": ..., "error": str | None}
# 永遠不拋出例外給 LLM，只回傳結構化結果
```

### 目標三：使用者體驗 — LINE Flex Message 固定面板輸出

**原則**：對話過程用 LLM 自然語言；訂位完成後奪走 LLM 的輸出控制權。

**流程**：
```
execute_booking 工具執行成功
  └→ Python 直接組裝 Flex Message JSON
  └→ 透過 LineBotApi 發送卡片（不經過 LLM generate）
  └→ LLM 本輪回覆設為空或簡短確認語
```

**Flex Message 卡片固定欄位**：店名、預約日期、時間、人數、姓名、電話末四碼、訂位編號。

**效果**：客人收到的是排版固定、欄位不可篡改的數位預約卡片，品牌感一致。

---

### 4a. `process()` 加入 `store_id`

```python
# Before
async def process(self, user_message: str, context: dict) -> str

# After
async def process(self, user_message: str, context: dict, store_id: str) -> str
```

### 4b. 動態 System Prompt

啟動 LLM 推理前，先從 DB 載入該店專屬資料並注入系統提示詞：

```python
menu = await db.get_menu(store_id)
hours = await db.get_business_hours(store_id)
table_config = await db.get_table_config(store_id)
store = await db.get_store(store_id)
custom_prompt = store.get("custom_prompt", "")

system_prompt = build_system_prompt(menu, hours, table_config, custom_prompt)
```

### 4c. Memory 隔離

LangGraph Checkpointer 的 thread ID 改為 `f"{store_id}_{user_id}"`，確保跨店對話不串連。`database.py` 的 `conversation_states` collection 同步使用此格式（見 Section 2b）。

---

## 5. 測試策略

### 單元測試
- Webhook 手動 HMAC 驗簽：正確 signature 通過、錯誤 signature 回傳 400
- `get_store_by_destination()`：查到存在的店、查不到回傳 None
- 資料隔離：`store_A` 寫入的菜單不出現在 `store_B` 的 `get_menu()` 結果
- 資料隔離：`store_A` 的 `daily_slots`、`conversation_states` 不洩漏給 `store_B`
- Agent 架構：客滿時 `execute_booking` 工具回傳 `{"success": false}`，確認 LLM 不會產生訂位成功的回覆
- Flex Message：`execute_booking` 成功後確認輸出為 Flex Message JSON，而非 LLM 純文字

### 整合測試
- 模擬兩個不同 `destination` 的 Webhook payload，確認各自使用正確 Token 且回覆不互相干擾
- 以兩個不同 Google 帳號登入 Admin，確認只看到自己店的資料

### 手動驗證
- 使用兩個 LINE 官方帳號各自傳送訊息，確認 AI 回應的菜單、營業時間各自正確
- 驗證錯誤 webhook signature 確實回傳 400，不進入處理流程

---

## 6. 實作邊界

### 必須做
- 所有 DB 讀寫強制傳入 `store_id`，無預設值
- Webhook 處理前必須完成 HMAC 驗簽
- Admin API 必須從 JWT 取出 `store_id`，不接受 client 傳入的 `store_id`
- `channel_secret` / `channel_access_token` 不得以明文存 Firestore

### 先確認再做
- 是否在 MVP 階段就使用 Secret Manager（或先暫存 Firestore 但限制 Rules）
- 桌位設定 Onboarding UI 的複雜度（簡單表單 vs 視覺化編輯器）

### 不做（此版本範圍外）
- 子域名路由（`store-a.domain.com`）
- 店家間的計費或用量限制
- 自動化的 TABLE_CONFIG 資料遷移腳本（現有單店資料手動搬移即可）
