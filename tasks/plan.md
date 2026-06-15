# Implementation Plan: LINE Marketplace 多租戶 SaaS

## Overview

Refactor `coffee_shop_agent` from a single-tenant app to a multi-tenant SaaS platform suitable for the LINE extension marketplace. Each store is identified by the `destination` field in LINE webhook payloads, which becomes the `store_id`. All data, credentials, and AI context are isolated per store. Admin authentication upgrades from a plaintext cookie to Google SSO + signed JWT.

## Architecture Decisions

- **Tenant key**: `destination` (LINE Bot ID) doubles as `store_id` — no extra field needed at the webhook level
- **Credential storage (MVP)**: `channel_secret` / `channel_access_token` stored as plaintext fields in `stores/{store_id}` in Firestore. Secret Manager is deferred post-MVP. The fields are named `channel_access_token` and `channel_secret` (not `*_ref`) to reflect this decision. Upgrade path: replace field reads with Secret Manager lookups later without changing callers.
- **Agent architecture**: Collapse the current 3-node relay (router → collector → rules → execute → responder) into a single ReAct agent with `bind_tools`; target <3s response time
- **Flex Message**: Python constructs the booking confirmation card directly after `execute_booking` succeeds — LLM is not in the card-rendering path

---

## Dependency Graph

```
stores/{store_id}  (Firestore model; channel_access_token + channel_secret stored plaintext)
        │
        ├── database.py  (all methods accept store_id)
        │        │
        │        ├── /callback webhook  (HMAC split + per-store LineBotApi)
        │        │
        │        ├── src/tools.py  (check_availability, execute_booking with store_id)
        │        │        │
        │        │        └── agents_graph.py  (ReAct loop + dynamic system prompt)
        │        │
        │        └── Admin routes  (JWT-gated, store_id from token)
        │
        └── Onboarding  (LINE OAuth → create store doc, Google SSO → bind admin_emails)
```

Implementation order: **bottom-up**. The database layer must be stable before anything else touches it.

---

## Task List

### Phase 1: Data Foundation

#### Task 1: `stores` collection + credential helpers

**Description:** Add the `stores/{store_id}` Firestore model and the three new `database.py` methods that the rest of the system depends on. Nothing else needs to change yet; this task makes it *possible* to look up a store by its LINE bot ID and retrieve its credentials.

**Acceptance criteria:**
- [ ] `get_store_by_destination(destination: str) -> Optional[Dict]` queries `stores` where `line_bot_id == destination`; returns the document dict or `None`
- [ ] `get_store_credentials(store_id: str) -> Tuple[str, str]` reads `channel_access_token` and `channel_secret` directly from `stores/{store_id}` in Firestore; returns `(channel_access_token, channel_secret)`
- [ ] `get_table_config(store_id: str) -> Dict` reads `stores/{store_id}/config/table_layout` and returns `{tables: {...}, total_capacity: int}`
- [ ] Unit tests cover: found store, missing store (returns None), credentials read, table config read

**Verification:**
- [ ] `pytest tests/test_database.py -k "store"` passes
- [ ] No changes to existing method signatures in this task

**Dependencies:** None

**Files likely touched:**
- `src/database.py`
- `tests/test_database.py`

**Estimated scope:** S (2 files)

---

#### Task 2: Add `store_id` to all `database.py` methods

**Description:** Thread `store_id` through every DB method so that reads and writes go to the correct tenant's sub-collection or prefixed document. This is a **breaking change** — all callers will temporarily break until Phase 2 and 3 are complete. Complete this task before touching callers.

Key mapping from SPEC §2b:
- `reservations` → add `store_id` field on write; filter by `store_id` on read
- `orders` → add `store_id` field
- `menu` → move to `stores/{store_id}/menu/` sub-collection
- `daily_slots` → doc ID becomes `{store_id}_{date}`
- `conversation_states` → doc ID becomes `{store_id}_{user_id}`
- `config/business_hours`, `config/special_closures`, `config/notifications` → move to `stores/{store_id}/config/`
- Remove `TABLE_CONFIG` and `TOTAL_CAPACITY` class attributes; use `get_table_config(store_id)` instead

**Acceptance criteria:**
- [ ] Every public `database.py` method signature now requires `store_id: str` as first or second parameter (after `self`)
- [ ] `TABLE_CONFIG` and `TOTAL_CAPACITY` removed from the class; `create_reservation`, `check_availability`, `get_available_floors`, `get_daily_occupied_tables` call `get_table_config(store_id)` dynamically
- [ ] `daily_slots` doc ID format: `{store_id}_{date}` (e.g. `Uabc123_2026-06-13`)
- [ ] `conversation_states` doc ID format: `{store_id}_{user_id}`
- [ ] Unit tests: store_A menu not visible to store_B; store_A slots not visible to store_B

**Verification:**
- [ ] `pytest tests/test_database.py` passes (may require test updates for new signatures)
- [ ] `grep -n "TABLE_CONFIG\|TOTAL_CAPACITY" src/database.py` returns nothing

**Dependencies:** Task 1

**Files likely touched:**
- `src/database.py`
- `tests/test_database.py`

**Estimated scope:** M (2 files, many method edits)

---

### Checkpoint: After Phase 1

- [ ] `pytest tests/test_database.py` all pass
- [ ] `grep -n "store_id" src/database.py | wc -l` shows coverage throughout the file
- [ ] Human review: confirm isolation logic is correct before proceeding

---

### Phase 2: Multi-Tenant Webhook

#### Task 3: Manual HMAC verification in `/callback`

**Description:** Replace the global `parser = WebhookParser(CHANNEL_SECRET)` initialization with per-request, per-store credential lookup and manual HMAC-SHA256 verification. Solves the chicken-and-egg problem described in SPEC §3b. Remove the global `CHANNEL_SECRET`, `CHANNEL_ACCESS_TOKEN`, `line_bot_api`, and `parser` variables from module scope.

The new flow:
1. Parse raw JSON body to get `destination` (no signature check yet)
2. Look up store by `destination`
3. Fetch `(channel_secret, channel_access_token)` from Secret Manager
4. Verify HMAC-SHA256 manually; return 400 on mismatch
5. Construct per-request `LineBotApi` and `WebhookParser` instances
6. Pass `store_id` to `handle_event`

**Acceptance criteria:**
- [ ] Correct signature → processes events normally
- [ ] Wrong signature → returns HTTP 400, no event processing
- [ ] Unknown `destination` → returns HTTP 404
- [ ] `CHANNEL_SECRET` and `CHANNEL_ACCESS_TOKEN` env vars no longer required at startup
- [ ] Unit tests cover all three cases above

**Verification:**
- [ ] `pytest tests/test_admin.py -k "webhook or callback or signature"` passes
- [ ] `grep -n "CHANNEL_SECRET\|CHANNEL_ACCESS_TOKEN\|parser\s*=" app.py` returns only comments or test fixtures

**Dependencies:** Tasks 1, 2

**Files likely touched:**
- `app.py`
- `tests/test_admin.py`

**Estimated scope:** M (2 files)

---

### Checkpoint: After Phase 2

- [ ] Full test suite passes
- [ ] Manual test: send a webhook with wrong signature → 400 confirmed
- [ ] Human review before touching the agent layer

---

### Phase 3: Agent Refactor (ReAct + Tools)

#### Task 4: `src/tools.py` — LangChain tool functions with `store_id`

**Description:** Create `src/tools.py` with two `@tool`-decorated async functions. Python handles all business logic; LLM only decides *when* to call them. These tools never raise exceptions to the LLM — all errors are returned as structured dicts.

```python
async def check_availability(store_id: str, date: str, time: str, pax: int) -> dict
    # returns {"available": bool, "error": str | None}

async def execute_booking(store_id: str, user_id: str, date: str, time: str,
                          pax: int, name: str, phone: str,
                          floor: int | None, allow_split: bool) -> dict
    # returns {"success": bool, "reservation_id": str | None,
    #          "tables": str | None, "error": str | None}
```

`execute_booking` also constructs and sends the LINE Flex Message confirmation card directly via `LineBotApi` when `success=True` (the `line_bot_api` instance is passed in via a closure or dependency injection; do **not** use a global).

**Acceptance criteria:**
- [ ] Both functions decorated with `@tool` (langchain_core)
- [ ] `execute_booking` with full valid params creates a reservation and returns `{"success": True, ...}`
- [ ] `execute_booking` when DB returns "overbooked" returns `{"success": False, "error": "該時段已客滿"}`
- [ ] When `execute_booking` succeeds, a Flex Message is sent (mock `LineBotApi` in tests, verify call was made)
- [ ] Unit tests cover success, overbooked, and validation-error paths

**Verification:**
- [ ] `pytest tests/test_tools.py` (new file) passes
- [ ] `from src.tools import check_availability, execute_booking` imports cleanly

**Dependencies:** Task 2

**Files likely touched:**
- `src/tools.py` (new)
- `tests/test_tools.py` (new)

**Estimated scope:** M (2 new files)

---

#### Task 5: Collapse relay architecture → single ReAct agent

**Description:** Rewrite `agents_graph.py` to a single `agent_node → tools_node → agent_node` ReAct loop using `llm.bind_tools([check_availability, execute_booking])`. Delete `router_node`, `booking_collector_node`, `business_rules_node`, `execute_booking_node`, `response_generator_node` and the complex `AgentState` TypedDict (replace with a minimal messages-only state). LangGraph's built-in `ToolNode` handles tool dispatch.

The `process()` method signature gains `store_id: str`:
```python
async def process(self, user_message: str, context: dict, store_id: str) -> str
```

`conversation_states` thread ID changes to `f"{store_id}_{user_id}"`.

**Acceptance criteria:**
- [ ] Graph has exactly 2 node types: `agent` and `tools` (standard ReAct pattern)
- [ ] `process(user_message, context, store_id)` works end-to-end (smoke test with mock tools)
- [ ] When `execute_booking` tool returns `success=False`, the LLM response does NOT say booking succeeded
- [ ] Conversation thread ID is `f"{store_id}_{user_id}"`
- [ ] `len(llm_calls_per_turn) <= 2` for a typical booking confirmation message

**Verification:**
- [ ] `pytest tests/test_agents_graph.py` passes (update existing tests)
- [ ] `grep -n "router_node\|booking_collector_node\|business_rules_node" src/agents_graph.py` returns nothing

**Dependencies:** Task 4

**Files likely touched:**
- `src/agents_graph.py`
- `tests/test_agents_graph.py`

**Estimated scope:** L (2 files, major rewrite)

---

#### Task 6: Dynamic system prompt from DB

**Description:** Before each agent invocation, load store-specific context from Firestore and inject it into the system prompt. This gives each store's AI its own menu, hours, floor layout, and custom tone.

```python
menu = await db.get_menu(store_id)
hours = await db.get_business_hours(store_id)
table_config = await db.get_table_config(store_id)
store = await db.get_store(store_id)   # new helper: reads stores/{store_id}
custom_prompt = store.get("custom_prompt", "")
system_prompt = build_system_prompt(menu, hours, table_config, custom_prompt)
```

**Acceptance criteria:**
- [ ] `build_system_prompt()` in `agents_graph.py` or a helper module produces a string containing menu items, business hours, floor info, and custom_prompt
- [ ] Two agents initialized with different `store_id`s produce different system prompts (verified in unit test with mocked DB)
- [ ] If store has no `custom_prompt`, system prompt still works correctly

**Verification:**
- [ ] `pytest tests/test_agents_graph.py -k "prompt"` passes
- [ ] Manual: agent for store_A sees store_A's menu in its prompt

**Dependencies:** Tasks 2, 5

**Files likely touched:**
- `src/agents_graph.py`
- `tests/test_agents_graph.py`

**Estimated scope:** S (1–2 files)

---

#### Task 7: Update `app.py` `handle_event` to pass `store_id`

**Description:** Wire the `store_id` extracted in `/callback` (Task 3) through to `langgraph_agent.process()`. Also update `send_admin_notification` to use the store's `notifications.admin_ids` config instead of the global env var.

**Acceptance criteria:**
- [ ] `handle_message_async(event, store_id, line_bot_api)` signature added
- [ ] `langgraph_agent.process(user_message, context, store_id)` called with correct `store_id`
- [ ] `/callback` passes `line_bot_api` instance (created per-request in Task 3) into the handler
- [ ] Seating map endpoint (`/seating-map`) passes `store_id` (from query param or session); fallback acceptable for MVP

**Verification:**
- [ ] `pytest tests/` all pass after this task
- [ ] Integration test: two webhook payloads with different `destination` values processed independently

**Dependencies:** Tasks 3, 5

**Files likely touched:**
- `app.py`
- `tests/test_admin.py`

**Estimated scope:** S (1–2 files)

---

### Checkpoint: After Phase 3

- [ ] Full test suite passes: `pytest tests/`
- [ ] Integration test: two different `destination` webhooks → each gets correct menu/hours in AI response
- [ ] Flex Message card confirmed sent on booking success (check logs or mock assertions)
- [ ] Human review before auth refactor

---

### Phase 4: Admin Authentication (Google SSO + JWT)

#### Task 8: Google OAuth callback + JWT issuance

**Description:** Add `GET /auth/google/callback` that exchanges an authorization code for a Google ID token, extracts the user's email, queries `stores` where `admin_emails` contains that email, issues a signed JWT (HS256, secret from env), and sets it as an `HttpOnly` cookie. If the email matches multiple stores, redirect to a store-selection page first.

Remove the `ADMIN_PASSWORD` env var and the `/admin/login` POST handler.

**Acceptance criteria:**
- [ ] `GET /admin` renders a "Sign in with Google" button (not a password form)
- [ ] `/auth/google/callback` with valid code → JWT cookie set → redirect to `/admin/dashboard`
- [ ] JWT contains `store_id` and `email` claims; signed with `JWT_SECRET` env var
- [ ] Invalid/missing JWT on any `/admin/*` route → redirect to `/admin`
- [ ] Unit tests: valid code flow (mock Google token exchange), unknown email returns 403

**Verification:**
- [ ] `pytest tests/test_admin.py -k "auth or jwt or google"` passes
- [ ] `grep -n "admin_session\|ADMIN_PASSWORD" app.py` returns nothing

**Dependencies:** Tasks 1, 7

**Files likely touched:**
- `app.py`
- `templates/login.html`
- `tests/test_admin.py`

**Estimated scope:** M (3 files)

---

#### Task 9: All admin routes read `store_id` from JWT

**Description:** Replace `request.cookies.get("admin_session") != "logged_in"` guards on every admin route with a JWT validation dependency. The JWT contains `store_id`; all DB calls use that value — the client never supplies `store_id` directly.

```python
async def get_current_store(request: Request) -> str:
    token = request.cookies.get("admin_token")
    payload = verify_jwt(token)  # raises HTTPException on failure
    return payload["store_id"]
```

**Acceptance criteria:**
- [ ] All `/admin/*` routes use `store_id: str = Depends(get_current_store)` 
- [ ] `db.get_all_reservations(store_id)`, `db.get_menu(store_id)`, etc. called with extracted `store_id`
- [ ] Admin from store_A cannot see store_B's reservations even with a valid JWT (wrong store_id in token)
- [ ] `admin_session` cookie references removed from all routes

**Verification:**
- [ ] `pytest tests/test_admin.py` passes
- [ ] `grep -rn "admin_session" app.py templates/` returns nothing

**Dependencies:** Task 8

**Files likely touched:**
- `app.py`
- `tests/test_admin.py`

**Estimated scope:** M (2 files, many route edits)

---

### Checkpoint: After Phase 4

- [ ] Full test suite passes
- [ ] Manual: sign in with Google, see only one store's data in dashboard
- [ ] Human review before onboarding flow

---

### Phase 5: Onboarding Flow

#### Task 10: LINE OAuth endpoint → create store document

**Description:** Add `GET /auth/line/callback` which receives the LINE marketplace OAuth code, exchanges it for a `channel_access_token`, calls the LINE Messaging API to get the bot's `destination` (bot ID), and creates or updates the `stores/{destination}` document in Firestore with plaintext credentials. Redirect to `/onboarding`.

**Acceptance criteria:**
- [ ] `GET /auth/line/callback?code=...` creates a `stores` document with `line_bot_id`, `channel_access_token`, `channel_secret`
- [ ] Credentials stored directly in Firestore (plaintext for MVP)
- [ ] If store already exists (same `destination`), update credentials rather than duplicate
- [ ] Unit test covers happy path with mocked LINE API

**Verification:**
- [ ] `pytest tests/test_onboarding.py -k "line_oauth"` passes
- [ ] Firestore `stores` doc has `channel_access_token_ref` (resource name string), not the token itself

**Dependencies:** Task 1

**Files likely touched:**
- `app.py`
- `tests/test_onboarding.py` (new)

**Estimated scope:** M (2 files)

---

#### Task 11: Onboarding form (store details + Google SSO binding)

**Description:** Add `GET /onboarding` (HTML form) and `POST /onboarding/complete` (form submit). The form collects store name, business hours (7-day grid), and initial table layout. On submit, update the `stores` document and bind the Google account's email to `admin_emails`. Redirect to `/admin/dashboard` when complete.

**Acceptance criteria:**
- [ ] `/onboarding` only accessible if a LINE authorization session cookie is present (set by Task 10)
- [ ] Form submission writes `name`, `config/business_hours`, `config/table_layout` to the store document
- [ ] Google SSO (reuse auth flow from Task 8) adds the authenticated email to `admin_emails`
- [ ] After completion, `/admin/dashboard` shows the new store's data

**Verification:**
- [ ] `pytest tests/test_onboarding.py` passes
- [ ] Manual: complete onboarding for a new bot → can immediately sign into admin dashboard

**Dependencies:** Tasks 8, 10

**Files likely touched:**
- `app.py`
- `templates/onboarding.html` (new)
- `tests/test_onboarding.py`

**Estimated scope:** M (3 files)

---

### Checkpoint: Final

- [ ] `pytest tests/` all pass
- [ ] Integration test: two different LINE accounts install the bot → each gets isolated data, isolated admin
- [ ] Manual: wrong webhook signature → 400; unknown destination → 404
- [ ] Manual: admin from store_A cannot access store_B data
- [ ] Flex Message booking card displays correctly in LINE
- [ ] Human sign-off before any production deployment

---

## Scope changes from open-question resolution

| Question | Decision | Impact |
|---|---|---|
| Secret Manager in MVP? | **No** — Firestore plaintext for now | `get_store_credentials` is a simple Firestore read; remove all `*_ref` field names |
| `/seating-map` | **Delete entirely** | Remove the route, its template, and all `get_daily_occupied_tables` calls from `app.py` |
| Existing data migration | **Not needed** — old data discarded | No migration script; T2 is purely additive for new stores |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Plaintext tokens in Firestore | Medium | Acceptable for MVP; add Firestore Security Rules to restrict reads to service account only; revisit post-MVP |
| `create_reservation` uses `TABLE_CONFIG` deeply (transactions) | Medium | Task 2 passes `table_config` dict into the transaction closure explicitly; test with a mock config |
| LangGraph `bind_tools` token count with large menus | Low | `build_system_prompt` truncates menu to 20 items max; test with large fixture |
| Google OAuth redirect_uri in production vs dev | Low | Use `OAUTH_REDIRECT_BASE_URL` env var in Tasks 8 and 10 |

---

## Phase 6: 店家管理 UI 擴充（服務價格、員工排班、設定總覽）

> Builds on top of the completed multi-tenant foundation (T0–T11). All routes already use `Depends(get_current_store)` for JWT auth and `store_id` isolation.

### Dependency Graph (Phase 6)

```
T12: Add price to menu  ──┐
T13: Employee DB funcs  ──┼──► T15: /admin/settings route
T14: Employee routes+UI ──┘         │
                                     ▼
                              T16: settings_dashboard.html + nav links
```

T12 and T13 are independent and can be done in parallel. T14 depends on T13. T15/T16 depend on T12 + T13 (so all data is readable).

---

#### Task 12: 服務項目加入「價錢」欄位 (F1)

**Description:** Add a `price: int` field (NTD) to the menu item schema. Three touch-points: the DB write function, two app routes, and the admin HTML template (table column + add form + edit modal).

**Acceptance criteria:**
- [ ] `add_menu_item(store_id, name, duration, price)` writes `price` to Firestore
- [ ] `POST /admin/menu/add` accepts `price: int = Form(...)` and passes it to DB
- [ ] `POST /admin/menu/update/{item_id}` accepts `price: int = Form(...)` and includes it in the update dict
- [ ] `menu_dashboard.html` shows a third column "價格" displaying `{{ item.price }} 元`
- [ ] Add form and edit modal each include a numeric `price` input field
- [ ] `get_menu()` return value includes `price` key (already stored in Firestore; no code change needed)

**Verification:**
- [ ] `pytest tests/` passes (no regressions)
- [ ] Manual: add a new service → price appears in the list; edit it → price updates

**Dependencies:** None (T0–T11 already complete)

**Files likely touched:**
- `src/database.py` (line 643: `add_menu_item`)
- `app.py` (lines 234, 239: two POST routes)
- `templates/menu_dashboard.html`

**Estimated scope:** S (3 files, small edits each)

---

#### Task 13: 員工上班時間 DB 函式 (F2 — backend)

**Description:** Add four CRUD methods to `DatabaseManager` for the `stores/{store_id}/employees/` sub-collection. No routes or UI yet — just the data layer, with mock-mode stubs.

```python
async def get_employees(self, store_id: str) -> List[Dict]
async def add_employee(self, store_id: str, name: str, schedule: dict) -> str
async def update_employee(self, store_id: str, emp_id: str, data: dict) -> bool
async def delete_employee(self, store_id: str, emp_id: str) -> bool
```

Schedule schema per employee:
```json
{ "Monday": {"start":"09:00","end":"17:00","off":false}, ... }
```

**Acceptance criteria:**
- [ ] All four methods exist and follow the same `if not self.client: return <stub>` mock pattern as other methods
- [ ] `get_employees()` returns list of dicts each with `id`, `name`, `schedule` keys
- [ ] `add_employee()` returns the Firestore doc ID string
- [ ] Data is isolated per `store_id` (path: `stores/{store_id}/employees/{emp_id}`)

**Verification:**
- [ ] `pytest tests/test_database.py` passes (add at least one unit test per function using mock)

**Dependencies:** None

**Files likely touched:**
- `src/database.py`
- `tests/test_database.py`

**Estimated scope:** S (2 files)

---

#### Task 14: 員工管理路由 + UI (F2 — frontend)

**Description:** Wire four admin routes and create `employees_dashboard.html`. The add form collects employee name + a 7-day schedule grid (checkbox for off-day, time inputs for start/end per working day). The list table shows name + working-day summary.

Routes:
```
GET  /admin/employees              → list page
POST /admin/employees/add          → create employee, redirect back
POST /admin/employees/update/{id}  → update employee, redirect back
POST /admin/employees/delete/{id}  → delete employee, redirect back
```

**Acceptance criteria:**
- [ ] `/admin/employees` lists all employees with name and working-day summary (e.g. "一 三 五")
- [ ] Add form creates employee and immediately appears in the list on redirect
- [ ] Delete button removes the employee from Firestore and from the list
- [ ] Edit (modal or inline) allows updating name and schedule
- [ ] Nav link "員工排班" added to `employees_dashboard.html` nav bar, consistent with other pages
- [ ] All routes use `store_id: str = Depends(get_current_store)` — no auth bypass

**Verification:**
- [ ] `pytest tests/` passes (no regressions)
- [ ] Manual: add 2 employees with different schedules → list shows correct working days

**Dependencies:** Task 13

**Files likely touched:**
- `app.py`
- `templates/employees_dashboard.html` (new)

**Estimated scope:** M (2 files)

---

### Checkpoint: After Tasks 12–14

- [ ] `pytest tests/` all pass
- [ ] `/admin/menu` shows name / duration / price correctly
- [ ] `/admin/employees` add/list/delete all work
- [ ] Human review before building the settings aggregation page

---

#### Task 15: 設定總覽頁 — route (`/admin/settings`)

**Description:** Add one new GET route that fetches data from all five existing DB functions and passes them to a new template. `get_store()` already exists at `database.py:35`.

```python
@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_overview(request: Request, store_id: str = Depends(get_current_store)):
    menu_items    = await db.get_menu(store_id)
    hours         = await db.get_business_hours(store_id)
    closures      = await db.get_special_closures(store_id)
    notifications = await db.get_notification_settings(store_id)
    store         = await db.get_store(store_id)
    employees     = await db.get_employees(store_id)
    return templates.TemplateResponse("settings_dashboard.html", {
        "request":     request,
        "menu_items":  menu_items,
        "hours":       hours,
        "days":        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
        "closures":    sorted(closures),
        "admin_ids":   notifications.get("admin_ids", []),
        "line_bot_id": store.get("line_bot_id", "") if store else "",
        "store_id":    store_id,
        "employees":   employees,
    })
```

**Acceptance criteria:**
- [ ] Route exists, is JWT-guarded, and returns 200 for a valid store session
- [ ] All six data vars passed to template context
- [ ] No new DB functions needed (`get_store` already exists at database.py:35)

**Verification:**
- [ ] `pytest tests/test_admin.py` passes (add a smoke test for `/admin/settings`)

**Dependencies:** Tasks 12, 13, 14

**Files likely touched:**
- `app.py`
- `tests/test_admin.py`

**Estimated scope:** XS (1 file)

---

#### Task 16: 設定總覽頁 UI + 全站導覽列更新

**Description:** Create `templates/settings_dashboard.html` with 6 read-only cards. Each card has the section title, data content, and an "✏️ 編輯" link to the corresponding management page. Also add a "⚙️ 設定總覽" nav link to all 5 existing admin templates.

Card layout:
| Card | 顯示 | 空值提示 | 編輯連結 |
|---|---|---|---|
| 服務項目 | name / duration / price 列表 | 尚未新增服務項目 | `/admin/menu` |
| 員工排班 | name + working-day summary | 尚未設定員工班表 | `/admin/employees` |
| 每週營業時間 | 每天 open–close，closed 顯示「休息」 | — (always has defaults) | `/admin/hours` |
| 公休日 | date tags | 尚未設定公休日 | `/admin/hours` |
| LINE 通知接收者 | LINE user ID list | 尚未設定通知接收者 | `/admin/notifications` |
| LINE Bot 狀態 | ✅ 已連線 Bot ID + Webhook URL / ❌ 未設定 | — | `/admin/line-settings` |

Nav update: add `<a href="/admin/settings">⚙️ 設定總覽</a>` to:
- `templates/dashboard.html`
- `templates/menu_dashboard.html`
- `templates/hours_dashboard.html`
- `templates/notifications_dashboard.html`
- `templates/employees_dashboard.html` (created in T14)

**Acceptance criteria:**
- [ ] All 6 cards render with correct data from the template context
- [ ] Empty-state messages show when collections are empty
- [ ] LINE Bot card shows ✅ when `line_bot_id` is non-empty, ❌ otherwise
- [ ] Each card's "✏️ 編輯" link points to the correct route
- [ ] "⚙️ 設定總覽" nav link appears in all 5 updated templates, with `active` class on the settings page itself
- [ ] Page is fully read-only (no `<input>`, `<form>`, or `<button>` elements)

**Verification:**
- [ ] `pytest tests/` passes
- [ ] Manual: visit `/admin/settings` → all 6 cards render; click each "編輯" link → lands on correct page

**Dependencies:** Task 15

**Files likely touched:**
- `templates/settings_dashboard.html` (new)
- `templates/dashboard.html`
- `templates/menu_dashboard.html`
- `templates/hours_dashboard.html`
- `templates/notifications_dashboard.html`
- `templates/employees_dashboard.html`

**Estimated scope:** M (6 files)

---

### Checkpoint: Phase 6 Complete

- [ ] `pytest tests/` all pass (no regressions from T0–T11)
- [ ] `/admin/menu` — 3 columns (name / duration / price)
- [ ] `/admin/employees` — add, list, delete working
- [ ] `/admin/settings` — all 6 cards render with real data
- [ ] Nav bar consistent across all admin pages
- [ ] Human review + sign-off
