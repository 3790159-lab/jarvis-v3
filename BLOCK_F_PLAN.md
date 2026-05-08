# BLOCK_F_PLAN.md — Production Polish + Mobile + Deployment

Date: 2026-05-01  
Status: planning  
Prerequisite: Block E complete (804 tests, 85+ commits total)

---

## ЧАСТЬ 1: Wire New Services (Phase 33) ~1h

### 1.A Auto-log decisions in run_intent

Integrate `decision_log.log_decision()` into `run_intent()` in smart telegram:
- Log every intent classification
- Attach message_id for feedback keyboard
- Add 👍/👎 keyboard to select responses

### 1.B Parallel execution in mesh

Integrate `parallel_executor.execute_parallel()` into mesh router:
- When plan has 2+ independent agents → run in parallel
- Real-time progress edit via `edit_message()`

### Tests (10+)

---

## ЧАСТЬ 2: Robustness (Phase 34) ~1.5h

### 2.A Error reporter integration

Wire `error_reporter` into all command handlers:
- Unhandled exceptions → logged + user notified
- Critical threshold: 3 errors/minute → Telegram alert

### 2.B Scheduler startup in bot

On bot start → `_get_scheduler().start()` to restore tasks

### 2.C Backend crash detection

Bot polls `/health` every 30s; if 3 consecutive failures → notify user

### Tests (15+)

---

## ЧАСТЬ 3: Production Deployment (Phase 35) ~2h

### 3.A Docker setup

```dockerfile
FROM python:3.12-slim
# bot + backend in one image
# supervisord manages both processes
```

### 3.B Auto-restart scripts

`start_jarvis.ps1` — Windows  
`start_jarvis.sh` — Linux/Mac

With health check loop + auto-restart on crash

### 3.C Environment validation on startup

Check all required env vars → friendly error if missing

### Tests (10+)

---

## ЧАСТЬ 4: Analytics (Phase 36) ~1.5h

### 4.A Daily auto-report

Daily summary sent at 23:59:
- Total requests, success rate
- Top 3 intents
- Errors count
- Cost estimate

### 4.B Improvement tracking

After `/improve analyze` → save suggestions to `state/improvement_log.json`
Track which suggestions were applied and whether metrics improved

### Tests (10+)

---

## ЧАСТЬ 5: Mobile + UX (Phase 37) ~1h

### 5.A Inline mode

Bot responds to @jarvis_bot queries inline (in any chat)

### 5.B Message templates

Common response formats (table, list, code) with consistent styling

### 5.C Rich media

Images sent with captions, not plain URLs

### Tests (5+)

---

## Estimated Time

| Phase | Hours |
|-------|-------|
| 33: Wire services | 1h |
| 34: Robustness | 1.5h |
| 35: Deployment | 2h |
| 36: Analytics | 1.5h |
| 37: Mobile UX | 1h |
| **Total** | **~7h** |

---

## Success Criteria

- [ ] Every intent auto-logged with decision_log
- [ ] Parallel mesh execution for 2+ independent agents
- [ ] Docker image builds and runs correctly
- [ ] Auto-restart on crash (3 crashes → manual intervention alert)
- [ ] Daily summary auto-sent at 23:59
- [ ] 850+ total tests
