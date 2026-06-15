# Task List: LINE Marketplace 多租戶 SaaS

## Phase 0: Cleanup (do first, no dependencies)
- [x] T0: Delete `/seating-map` route + `seating_map.html` template — `app.py`, `templates/seating_map.html`

## Phase 1: Data Foundation
- [x] T1: `stores` collection + `get_store_by_destination()` + `get_store_credentials()` (Firestore plaintext) + `get_table_config()` — `src/database.py`, `tests/test_database.py`
- [x] T2: Add `store_id` to all `database.py` methods; remove `TABLE_CONFIG`/`TOTAL_CAPACITY`; update doc ID formats — `src/database.py`, `tests/test_database.py`

## Checkpoint A ✅
```bash
python -m pytest tests/test_database.py -v
grep -n "store_id" src/database.py | wc -l   # 確認覆蓋率
grep -n "TABLE_CONFIG\|TOTAL_CAPACITY" src/database.py  # 應無結果
```
- [x] 所有測試通過

## Phase 2: Multi-Tenant Webhook
- [x] T3: Manual HMAC verification in `/callback`; remove global `parser`/`line_bot_api` init — `app.py`, `tests/test_admin.py`

## Checkpoint B ✅
```bash
python -m pytest tests/test_admin.py tests/test_database.py -v

# 手動驗證（需 live server + 真實 Firestore）：
# 錯誤簽名 → 應得 400
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/callback \
  -H "Content-Type: application/json" \
  -H "X-Line-Signature: bad_sig" \
  -d '{"destination":"<your_bot_id>","events":[]}'

# 未知 destination → 應得 404
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/callback \
  -H "Content-Type: application/json" \
  -H "X-Line-Signature: any" \
  -d '{"destination":"nonexistent","events":[]}'

# 確認全域變數已移除
grep -n "CHANNEL_SECRET\|CHANNEL_ACCESS_TOKEN\|^parser\s*=\|^line_bot_api\s*=" app.py  # 應無結果
```
- [x] 所有測試通過；wrong-signature → 400 確認

## Phase 3: Agent Refactor
- [x] T4: `src/tools.py` — `check_availability` + `execute_booking` as LangChain `@tool`s with `store_id` + Flex Message send — `src/tools.py` (new), `tests/test_tools.py` (new)
- [x] T5: Collapse relay → single ReAct agent (`bind_tools`), update `process()` signature, update thread ID format — `src/agents_graph.py`, `tests/test_agents_graph.py`
- [x] T6: Dynamic system prompt from DB (menu, hours, table_config, custom_prompt) — `src/agents_graph.py`, `tests/test_agents_graph.py`
- [x] T7: Wire `store_id` from `/callback` through `handle_event` → `process()` — `app.py`, `tests/test_admin.py`

## Checkpoint C ✅
```bash
python -m pytest tests/ -v

# 整合驗證（需 live server + 兩個不同 store 的 Firestore 文件）：
# 確認 store_A / store_B 各自收到自己的 AI 回應（menu/hours 隔離）
# 確認 execute_booking 成功時 Flex Message 有送出（看 server log）

grep -n "router_node\|booking_collector_node\|business_rules_node" src/agents_graph.py  # 應無結果
```
- [x] 所有測試通過

## Phase 4: Admin Auth (Google SSO + JWT)
- [x] T8: `GET /auth/google/callback` + JWT issuance + remove password login — `app.py`, `templates/login.html`, `tests/test_admin.py`
- [x] T9: All `/admin/*` routes use `Depends(get_current_store)` JWT guard — `app.py`, `tests/test_admin.py`

## Checkpoint D ✅
```bash
python -m pytest tests/ -v

# 手動驗證（需 Google OAuth 設定）：
# 1. 用 store_A 的 Google 帳號登入 → 只看到 store_A 的訂位
# 2. 嘗試直接帶 store_B 的 store_id 存取 /admin/dashboard → 應被擋住

grep -rn "admin_session" app.py templates/  # 應無結果（已改為 JWT）
```
- [x] 所有測試通過；`admin_session` 已完全移除

## Phase 5: Onboarding
- [x] T10: `GET /auth/line/callback` → create store doc (plaintext credentials) — `app.py`, `tests/test_onboarding.py` (new)
- [x] T11: `/onboarding` form + Google SSO binding + `admin_emails` — `app.py`, `templates/onboarding.html` (new), `tests/test_onboarding.py`

## Checkpoint E (Final)
```bash
python -m pytest tests/ -v

# 端對端手動驗證：
# 1. 新 LINE Bot 走完 /auth/line/callback → Firestore 建立 store doc
# 2. Google SSO 綁定 admin_emails → /admin/dashboard 可正常登入
# 3. 用兩個不同 LINE Bot 各自訂位 → 資料完全隔離
# 4. 訂位成功 → LINE 收到 Flex Message 確認卡
# 5. 錯誤 webhook 簽名 → 400；未知 destination → 404
```
- [ ] 端對端手動驗證 — human sign-off before production deployment

## Phase 6: 店家管理 UI 擴充

> 前置條件：T0–T11 全部完成 ✅

- [x] T12: 服務項目加「價錢」欄位 — `src/database.py` (add_menu_item), `app.py` (2 routes), `templates/menu_dashboard.html`
- [x] T13: 員工 DB CRUD — `src/database.py` (4 new methods), `tests/test_database.py`
- [x] T14: 員工管理路由 + UI — `app.py` (4 routes), `templates/employees_dashboard.html` (new)
- [x] T15: `/admin/settings` route — `app.py` (1 route), `tests/test_admin.py`
- [x] T16: 設定總覽 UI + 全站導覽列 — `templates/settings_dashboard.html` (new), 5 existing templates

## Checkpoint F (Phase 6 Complete)
```bash
python -m pytest tests/ -v

# 手動確認：
# 1. /admin/menu — 新增服務含 price → 列表顯示三欄
# 2. /admin/employees — 新增/列出/刪除員工
# 3. /admin/settings — 6 個 card 全部顯示正確資料
# 4. 導覽列所有頁面均出現「設定總覽」tab
```
- [x] 所有測試通過（61 passed，含 22 個新增的 T12–T16 專屬測試）；human sign-off 待確認

## 本輪變更（2026-06-15）

- [x] 修正 LLM model（`gemini-2.0-flash` → `gemini-2.0-flash-001`，修 Cloud Run NOT_FOUND 錯誤）
- [x] 移除 LangGraph，改用 `bind_tools` 直接 ReAct loop（`src/agents_graph.py`）
- [x] 移除 `langgraph>=0.2.0` 從 `requirements.txt`
- [x] `_BASE_PROMPT` 重寫（按摩店語境、一次問齊風格，移除樓層說明）
- [x] `store_id` / `user_id` 注入 system prompt（工具呼叫可取得正確值）
- [x] 全站 container 統一 `max-width: 1000px`（5 個 templates，切頁不跳寬）
- [x] 員工「老闆娘」新增（Firestore 直寫，每天 12:00–22:00）
- [x] 服務項目「洗頭 NT$500/40min」「挖耳朵 NT$400/30min」新增（Firestore 直寫）
- [ ] 部署至 Cloud Run（`bash deploy.sh`）
- [ ] Checkpoint E / F 人工 sign-off
