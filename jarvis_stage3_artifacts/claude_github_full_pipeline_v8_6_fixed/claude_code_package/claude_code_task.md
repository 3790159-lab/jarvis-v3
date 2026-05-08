# Claude Code Task — Jarvis Operator Dashboard V8.6 Fixed

Objective: improve the real Operator Dashboard integration created in V8.5.

Current working endpoints:
- GET /api/operator-dashboard/health
- GET /api/operator-dashboard/summary
- GET /api/operator-dashboard/queue
- GET /api/operator-dashboard/evidence
- GET /api/operator-dashboard/gateway
- GET /api/operator-dashboard/full-creator
- GET /api/operator-dashboard/self-healing

Tasks:
1. Review app/routers/operator_dashboard.py
2. Add /api/operator-dashboard/artifacts
3. Add /api/operator-dashboard/night-mode
4. Keep read-only only
5. Do not expose secrets
6. Do not push automatically
