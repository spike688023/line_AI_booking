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

---

# SPEC: 服務項目、員工排班、公休設定

> **新增功能規格（疊加於多租戶 SaaS 架構之上）**

---

## 目標

讓店家透過 Admin Dashboard 管理三類資料：
1. **服務項目**：名稱 + 時間（分鐘）+ 價錢 → AI 回覆客人時可準確報價
2. **員工上班時間**：每位員工每週哪幾天、幾點到幾點 → AI 排班時不排空班
3. **公休日**（已存在）：特定日期全店休息 → 已實作，此規格僅補文件

---

## 現況盤點

| 功能 | 現況 | 需要做的事 |
|---|---|---|
| 服務項目 | 有 `name` + `duration`，**缺 `price`** | 加欄位：DB、routes、UI |
| 員工上班時間 | **完全沒有** | 新增：DB model、CRUD、UI |
| 公休日 | **已完成** | 無需改動 |

---

## F1：服務項目加入「價錢」欄位

### 資料模型

Firestore path：`stores/{store_id}/menu/{item_id}`

```
{
  name:       string      # 服務名稱（例：全身精油按摩）
  duration:   int         # 服務時間（分鐘）
  price:      int         # 價格（台幣，整數）  ← 新增
  created_at: timestamp
}
```

### 需修改的程式碼

**[src/database.py](src/database.py)**
- `add_menu_item(store_id, name, duration)` → 加 `price: int` 參數，寫入 Firestore
- `update_menu_item()` 已接收 dict，不需改介面

**[app.py](app.py)**
- `POST /admin/menu/add`（line 234）：加 `price: int = Form(...)`，傳給 `db.add_menu_item()`
- `POST /admin/menu/update/{item_id}`（line 239）：加 `price: int = Form(...)`，寫入 dict

**[templates/menu_dashboard.html](templates/menu_dashboard.html)**
- 表格加「價格」欄（顯示 `{{ item.price }} 元`）
- 新增表單加 `<input type="number" name="price">` 欄位
- 編輯 Modal 加 price 輸入框

### 驗收標準

- [ ] 新增服務項目時可填入價錢
- [ ] 列表顯示「服務名稱 / 時間 / 價錢」三欄
- [ ] 編輯時可修改價錢
- [ ] `get_menu()` 回傳的 dict 包含 `price` 欄位（供 AI System Prompt 使用）

---

## F2：員工上班時間管理

### 資料模型

Firestore path：`stores/{store_id}/employees/{employee_id}`

```
{
  name:     string    # 員工姓名
  schedule: {
    Monday:    { start: "09:00", end: "17:00", off: false }
    Tuesday:   { start: "09:00", end: "17:00", off: false }
    Wednesday: { start: "09:00", end: "17:00", off: false }
    Thursday:  { start: "09:00", end: "17:00", off: true  }  # off=true 表示休假
    Friday:    { start: "09:00", end: "17:00", off: false }
    Saturday:  { start: "10:00", end: "18:00", off: false }
    Sunday:    { off: true }                                  # 整天休
  }
}
```

### 新增 DB 函式（[src/database.py](src/database.py)）

```python
async def get_employees(self, store_id: str) -> List[Dict]
# 回傳 stores/{store_id}/employees/ 下所有 docs

async def add_employee(self, store_id: str, name: str, schedule: dict) -> str
# 新增員工，回傳 doc id

async def update_employee(self, store_id: str, emp_id: str, data: dict) -> bool
# 更新員工資料（name 或 schedule）

async def delete_employee(self, store_id: str, emp_id: str) -> bool
# 刪除員工
```

### 新增路由（[app.py](app.py)）

```
GET  /admin/employees            → 顯示員工列表頁
POST /admin/employees/add        → 新增員工（name + schedule form）
POST /admin/employees/update/{id} → 更新員工
POST /admin/employees/delete/{id} → 刪除員工
```

### 新增頁面 `templates/employees_dashboard.html`

UI 結構：
```
員工列表（表格）：姓名 | 上班日 | 操作（編輯/刪除）
新增員工區塊：
  - 員工姓名
  - 每天的 checkbox（是否上班）+ 時間輸入（start/end）
```

導覽列加入「員工管理」tab，與其他頁保持一致。

### AI 整合（供參考，非此 spec 實作範圍）

`get_employees()` 的回傳資料未來可注入 System Prompt，讓 AI 知道「週四下午沒員工」而拒絕排班。

### 驗收標準

- [ ] 可新增員工並設定每週各天的上班時間
- [ ] 可標記某天為休假（不顯示 start/end）
- [ ] 員工列表顯示姓名與上班天數摘要
- [ ] 可編輯、刪除員工
- [ ] 資料存於 `stores/{store_id}/employees/` sub-collection（多租戶隔離）

---

## F3：公休日（已完成，僅文件）

Firestore path：`stores/{store_id}/config/special_closures`

- `GET /admin/hours` → 列出現有公休日、提供新增表單
- `POST /admin/closures/add` → 新增日期
- `POST /admin/closures/remove` → 移除日期

**無需改動任何程式碼。**

---

## 實作順序

1. **F1 Price 欄位**（改動最小，3 個檔案）
2. **F2 Employee CRUD（database.py + app.py）**
3. **F2 Employee UI（employees_dashboard.html）**

---

## 不做（此 spec 範圍外）

- 員工請假申請流程（非店家管理介面）
- 班表衝突自動警告 UI
- AI 自動排班（員工時間只作為 System Prompt 的 context，由 AI 判斷）

---

# SPEC: 店家設定總覽頁（Settings Overview）

---

## 目標

在 `/admin/settings` 新增一個**唯讀總覽頁**，讓店家登入後能一眼確認所有已設定的資料，不需逐一進各設定頁翻找。點擊各區塊右上角的「編輯」連結即可跳到對應管理頁。

**目標使用者**：已完成初步設定的店家，想確認「之前設定的東西還在不在」。

---

## 頁面結構（`/admin/settings`）

6 個 Card 卡片，依序排列：

| # | Card 標題 | 資料來源 | 編輯連結 |
|---|---|---|---|
| 1 | 服務項目 | `get_menu()` | `/admin/menu` |
| 2 | 員工上班時間 | `get_employees()`（F2 完成後） | `/admin/employees` |
| 3 | 每週營業時間 | `get_business_hours()` | `/admin/hours` |
| 4 | 公休日 | `get_special_closures()` | `/admin/hours` |
| 5 | 通知設定（LINE 管理員） | `get_notification_settings()` | `/admin/notifications` |
| 6 | LINE 設定狀態 | `get_store()` 的 `line_bot_id` 欄位 | `/admin/line-settings` |

### Card 1：服務項目

```
服務項目（3 項）                         [編輯]
─────────────────────────────────────
全身精油按摩     90 分鐘    $1,800
頭部舒壓         30 分鐘    $  600
腳底按摩         60 分鐘    $1,200
```

若無項目：顯示「尚未新增服務項目」灰色提示。

### Card 2：員工上班時間

```
員工排班（2 人）                          [編輯]
─────────────────────────────────────
小美   一 三 五  09:00–17:00
小偉   二 四 六  10:00–18:00
```

若 F2 尚未實作（員工 collection 為空）：顯示「尚未設定員工班表」。

### Card 3：每週營業時間

```
每週營業時間                              [編輯]
─────────────────────────────────────
週一  09:00 – 18:00
週二  09:00 – 18:00
週三  09:00 – 18:00
週四  09:00 – 18:00
週五  09:00 – 18:00
週六  10:00 – 20:00
週日  休息
```

### Card 4：公休日

```
公休日（2 天）                            [編輯]
─────────────────────────────────────
2026-01-01   2026-02-08
```

若無公休日：顯示「尚未設定公休日」。

### Card 5：通知設定

```
LINE 通知接收者（1 人）                   [編輯]
─────────────────────────────────────
Uxxxxxxxxxxxxxxxxx
```

若無：顯示「尚未設定通知接收者」。

### Card 6：LINE Bot 狀態

```
LINE Bot 連線狀態                         [編輯]
─────────────────────────────────────
✅ 已連線   Bot ID: Cxxxxxxxx
Webhook URL: https://.../{store_id}
```

若 `line_bot_id` 為空：顯示「❌ 尚未設定 LINE 憑證」紅色提示。

---

## 導覽列修改

所有 Admin 頁面的導覽列新增「設定總覽」tab：

```
訂位管理 | 服務項目 | 員工排班 | 營業時間 | 通知設定 | ⚙️ 設定總覽
```

---

## 需修改的程式碼

### [app.py](app.py) — 新增 1 個 route

```python
@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_overview(request: Request, store_id: str = Depends(get_current_store)):
    menu_items      = await db.get_menu(store_id)
    hours           = await db.get_business_hours(store_id)
    closures        = await db.get_special_closures(store_id)
    notifications   = await db.get_notification_settings(store_id)
    store           = await db.get_store(store_id)
    # employees = await db.get_employees(store_id)  # 待 F2 完成後解注
    return templates.TemplateResponse("settings_dashboard.html", {
        "request":      request,
        "menu_items":   menu_items,
        "hours":        hours,
        "days":         ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
        "closures":     sorted(closures),
        "admin_ids":    notifications.get("admin_ids", []),
        "line_bot_id":  store.get("line_bot_id", "") if store else "",
        "store_id":     store_id,
    })
```

### [templates/settings_dashboard.html](templates/settings_dashboard.html) — 新建

- 沿用其他頁的 Picnic CSS + `.container` 樣式
- 每個 Card 用 `border: 1px solid #eee; border-radius: 8px; padding: 16px; margin-bottom: 16px;` 包裹
- Card 右上角放 `<a href="...">✏️ 編輯</a>` 小連結
- 資料唯讀（無表單、無 input）

### 其他頁的導覽列

6 個 HTML template（dashboard、menu、hours、notifications、line-settings、employees）各加一條 nav link：

```html
<a href="/admin/settings" {% if active == 'settings' %}class="active"{% endif %}>⚙️ 設定總覽</a>
```

---

## `get_store()` 函式確認

確認 `database.py` 是否已有 `get_store(store_id)` 函式；若無需新增：

```python
async def get_store(self, store_id: str) -> Optional[Dict]:
    doc = self.client.collection("stores").document(store_id).get()
    return doc.to_dict() if doc.exists else None
```

---

## 驗收標準

- [ ] `/admin/settings` 頁面可正常載入，不需額外登入步驟
- [ ] 顯示服務項目清單（名稱 / 時間 / 價格）
- [ ] 顯示每週營業時間，休息日標記「休息」
- [ ] 顯示公休日列表；若無則顯示提示文字
- [ ] 顯示 LINE 通知接收者列表；若無則顯示提示文字
- [ ] 顯示 LINE Bot 連線狀態（有 `line_bot_id` 為綠色 ✅，無為紅色 ❌）
- [ ] 各 Card 的「編輯」連結正確指向對應管理頁
- [ ] 所有 Admin 頁導覽列新增「設定總覽」tab

---

## 不做（此 spec 範圍外）

- 在此頁直接行內編輯（點編輯就跳轉到管理頁，不做 inline edit）
- 設定的版本歷史或 diff 比較
- 員工排班 Card 的完整實作（F2 完成前顯示佔位提示即可）

---

# SPEC: 按摩店 Agent Prompt 重寫 ＋ 員工新增

> 變更日期：2026-06-15

---

## T1：重寫 `_BASE_PROMPT`（按摩店版本）

### 動機

原 prompt 寫死咖啡店樓層邏輯，不適用按摩店。改為：
1. 移除所有樓層資訊（`floor` 由系統自動分配，LLM 不詢問）
2. 對話風格改為「**一次問齊，缺什麼補什麼**」，而非逐題詢問

### 現有 Tool 規格（不改）

| Tool | 參數 | 用途 |
|---|---|---|
| `check_availability` | `store_id, date, time, pax` | 確認指定時段是否有空位 |
| `execute_booking` | `store_id, user_id, date, time, pax, name, phone, floor, allow_split` | 建立訂位並觸發 Flex Message |

`floor` 傳 `None`，`allow_split` 傳 `false`，皆由系統處理，LLM 不需向客人詢問。

### 新 `_BASE_PROMPT` 內容

```
你是 AI 訂位助理。

【訂位流程】
一次詢問所有需要的資訊，有缺漏再補問：
  服務項目、人數、日期、時間、姓名、電話
資訊齊全後才呼叫工具。

【工具使用規則】
- check_availability：日期/時間/人數確認後立即呼叫，不可憑感覺回答有無空位
- execute_booking：check_availability 確認有空位後才呼叫，floor 傳 None
- 工具回傳 success=False：誠實告知客人失敗原因，禁止謊稱訂位成功

【回應規則】
1. 使用繁體中文
2. 親切有禮
3. 訂位完成後不需自行報訂位編號，系統會送出確認訊息
```

### 動態附加（`build_system_prompt`，無需改程式碼）

- `【服務項目】`：DB 菜單，格式 `名稱 NT$價格 (時間 min)`
- `【營業時間】`：每週各天
- `【店家特別指示】`：custom_prompt 欄位

### 需修改的檔案

- **`src/agents_graph.py`**：替換 `_BASE_PROMPT` 字串

### 驗收標準

- [ ] AI 不再提到樓層相關字眼
- [ ] 開場一次詢問所有訂位資訊，而非逐題問
- [ ] 資訊不齊時補問缺漏欄位
- [ ] 資訊齊全後呼叫 `check_availability`，有空位才 `execute_booking`
- [ ] 客滿時如實告知，不謊稱成功

---

## T3：新增服務項目

| 服務名稱 | 價格 | 時間 |
|---|---|---|
| 洗頭 | NT$500 | 40 min |
| 挖耳朵 | NT$400 | 30 min |

### 實作方式

呼叫 `db.add_menu_item(store_id, name, duration, price)`，store_id = `1e6f6c9e76d1`。

### 驗收標準

- [ ] `/admin/menu` 顯示「洗頭 NT$500 / 40分鐘」
- [ ] `/admin/menu` 顯示「挖耳朵 NT$400 / 30分鐘」
- [ ] AI system prompt 的 `【服務項目】` 區塊包含這兩項

---

## T2：新增員工「老闆娘」

### 資料

| 欄位 | 值 |
|---|---|
| 姓名 | 老闆娘 |
| 上班時間 | 每天 12:00 – 22:00 |
| store_id | `1e6f6c9e76d1`（spike688023@gmail.com 的 MD5 前 12 碼） |

### Firestore 寫入格式

```
stores/1e6f6c9e76d1/employees/{auto_id}
{
  name: "老闆娘",
  schedule: {
    Monday:    { start: "12:00", end: "22:00" },
    Tuesday:   { start: "12:00", end: "22:00" },
    Wednesday: { start: "12:00", end: "22:00" },
    Thursday:  { start: "12:00", end: "22:00" },
    Friday:    { start: "12:00", end: "22:00" },
    Saturday:  { start: "12:00", end: "22:00" },
    Sunday:    { start: "12:00", end: "22:00" }
  },
  created_at: SERVER_TIMESTAMP
}
```

### 實作方式

呼叫現有 `POST /admin/employees/add` API（Cloud Run endpoint），或直接呼叫 `db.add_employee()`。

### 驗收標準

- [ ] `/admin/employees` 頁面顯示「老闆娘」
- [ ] 排班顯示週一至週日皆有上班
- [ ] 上班時間 12:00 – 22:00
