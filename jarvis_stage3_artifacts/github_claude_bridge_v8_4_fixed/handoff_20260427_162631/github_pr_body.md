# Jarvis Operator Dashboard Read-Only API

## Summary
Adds read-only dashboard endpoints for Jarvis operator visibility.

## Safety
- No secrets
- No destructive endpoints
- No external writes
- No auto-push

## Proposed endpoints
- GET /api/operator-dashboard/health
- GET /api/operator-dashboard/queue
- GET /api/operator-dashboard/evidence
- GET /api/operator-dashboard/gateway
- GET /api/operator-dashboard/full-creator