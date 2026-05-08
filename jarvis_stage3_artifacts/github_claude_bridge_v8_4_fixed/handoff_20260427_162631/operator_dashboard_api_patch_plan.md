# Jarvis V8.4 Fixed — GitHub + Claude Code Bridge

## Status
This is a safe fixed handoff package. It does not run external code, does not push to GitHub, and does not modify backend files automatically.

## GitHub
Confirmed ChatGPT GitHub connector:
- login: 3790159-lab
- repo candidates:
  - 3790159-lab/ai-supervisor-backend
  - 3790159-lab/WEB.INDUSTRIES

## Claude Code task
Implement read-only Operator Dashboard API.

## Files to create/update
1. Create app/routers/operator_dashboard.py
2. Update app/main.py to include router
3. Add compile/smoke checks

## Validation
- python -m py_compile app/main.py
- python -m py_compile app/routers/operator_dashboard.py
- GET /api/operator-dashboard/health
- GET /api/operator-dashboard/queue
- GET /api/operator-dashboard/evidence