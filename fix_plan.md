# AxenWP Fix Plan

## Priority: HIGH

### 1. [DONE] Add chat history cleanup to prevent unbounded table growth
- **Problem:** `chat_histories` table grows indefinitely — no TTL or cleanup mechanism
- **Impact:** DB storage bloat, slower queries over time, potential production outage
- **Fix:** Add a scheduled cleanup job that removes entries older than 30 days
- **Files:** `main.py`, `services/ai_service.py` or new cleanup utility

### 2. [DONE] Async DB operations in chat history (sync blocking event loop)
- **Problem:** `PostgresChatMessageHistory.messages`, `add_user_message`, `add_ai_message` use sync `SessionLocal()` inside async `generate_response()`, blocking the event loop
- **Impact:** Increased latency on every AI response, reduced throughput under load
- **Fix:** Convert to async sessions or run sync operations in `asyncio.to_thread()`

### 3. [DONE] Health endpoint should verify database connectivity
- **Problem:** `/health` only lists tenants, doesn't verify DB is reachable
- **Impact:** Health checks pass even when DB is down, misleading monitoring
- **Fix:** Add a simple `SELECT 1` check to the health endpoint

## Priority: MEDIUM

### 4. [DONE] Replace deprecated `datetime.utcnow()` calls
- **Problem:** `datetime.utcnow()` is deprecated in Python 3.12+ (used in models.py defaults)
- **Impact:** Deprecation warnings, eventual removal in future Python versions
- **Fix:** Replace with `datetime.now(timezone.utc)`

### 5. [DONE] Remove unused `always_reply_with_audio` column
- **Problem:** Column exists in AIAgent model but is never read in application code (replaced by smart audio detection)
- **Impact:** Dead code / schema confusion
- **Fix:** Migration to drop column + remove from model

### 6. [DONE] Add test infrastructure
- **Problem:** Zero test files in the project
- **Impact:** No regression safety net, risky deploys
- **Fix:** Add pytest setup + initial tests for core services (ai_service, token_manager)

## Priority: LOW

### 7. [DONE] Shared httpx client for GHL/Z-API services
- **Problem:** New `httpx.AsyncClient` created per-request in ghl_service and zapi_service
- **Impact:** Connection overhead, no HTTP/2 multiplexing or keep-alive benefits
- **Fix:** Use a module-level or app-level shared client with proper lifecycle

### 8. [DONE] Debounce dict memory management
- **Problem:** `_ai_pending_tasks`, `_ai_message_buffers`, `_ai_debounce_config` dicts in zapi_receiver could accumulate stale entries if tasks fail unexpectedly
- **Impact:** Minor memory leak over long uptimes
- **Fix:** Add periodic cleanup or use WeakValueDictionary patterns
