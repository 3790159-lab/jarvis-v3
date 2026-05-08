# Jarvis Real Action Evidence Report

- Run ID: `evidence_20260427_131817`
- Created: `2026-04-27T13:18:17`
- Backend status: `partial`
- Backend OK endpoints: `5/6`
- n8n port open: `True`
- Truth Guard verdict: `passed_with_evidence`
- Strict next action: `continue_autonomous_low_risk_tasks`

## Evidence
- ✅ backend_endpoints_responded
- ✅ main_app_file_exists
- ✅ artifact_root_exists
- ✅ n8n_port_open

## Gaps
- No critical gaps detected.

## Backend endpoint checks
- ❌ `http://127.0.0.1:8015/` status=`404` elapsed_ms=`74`
- ✅ `http://127.0.0.1:8015/health` status=`200` elapsed_ms=`17`
- ✅ `http://127.0.0.1:8015/api/ai/health` status=`200` elapsed_ms=`7`
- ✅ `http://127.0.0.1:8015/api/autonomy/health` status=`200` elapsed_ms=`3`
- ✅ `http://127.0.0.1:8015/api/autonomy/mission-bridge/health` status=`200` elapsed_ms=`20`
- ✅ `http://127.0.0.1:8015/api/provider-routing/health` status=`200` elapsed_ms=`6`

## n8n checks
- Port 5678 open: `True`
- ✅ `http://127.0.0.1:5678/healthz` status=`200` error=`None`
- ✅ `http://127.0.0.1:5678/rest/settings` status=`200` error=`None`
