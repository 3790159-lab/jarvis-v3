# Claude Code Task

Implement the read-only Operator Dashboard API.

## Required files
- app/routers/operator_dashboard.py
- app/main.py

## Constraints
- No secret exposure
- No destructive endpoints
- No external writes
- Minimal reversible changes

## Validation
Run:
python -m py_compile app/main.py
python -m py_compile app/routers/operator_dashboard.py