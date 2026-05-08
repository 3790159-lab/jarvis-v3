# Phase 11 — Execution Orchestrator / Mission Runner v2

## Purpose
Move Jarvis from static planning into real staged mission execution.

## Main goals
- execute mission stages step-by-step
- support stage transitions
- support stop / resume / cancel
- maintain live execution journal
- finalize mission with completion logic

## Core components
- planner integration
- stage dispatcher
- executor
- QA validation stage
- execution journal
- run completion handler

## Expected API direction
- POST /api/missions/{mission_id}/run
- POST /api/missions/{mission_id}/stop
- POST /api/missions/{mission_id}/resume
- POST /api/missions/{mission_id}/cancel
- GET  /api/missions/{mission_id}/journal

## Risks
- stage deadlock
- repeated retries without progress
- missing completion reconciliation
- broken journal state after restart

## Acceptance criteria
- mission can move through all stages
- journal reflects current stage and result
- stop/resume/cancel work predictably
- completion status is written correctly
