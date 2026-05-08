# Director Lifecycle Tuneup

- Generated at: 2026-04-22T13:49:32Z
- Kind: director_lifecycle_tuneup
- Mode: plan_only
- Reason: repeated bind/recovery/log signals or reliability focus

## Candidate files
- app/jarvis_local_n8n_director_pro_core.py

## Observed signals
- log_patterns: {"bind_error": 21, "exception": 23}
- log_error_lines: 9

## Safe implementation constraints
- keep restart/port guard logic idempotent
- avoid breaking existing endpoints
- prefer additive lifecycle/reporting changes
- require compile + health + smoke verification before merge

## Recommended next manual step
- review this plan and approve an implementation block explicitly
