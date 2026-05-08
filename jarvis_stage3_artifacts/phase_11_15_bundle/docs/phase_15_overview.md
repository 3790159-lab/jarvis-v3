# Phase 15 — Human-in-the-Loop Console / Approval Dashboard

## Purpose
Add a practical human approval and oversight layer for autonomous actions.

## Main goals
- create approval endpoint or panel
- show reasons, risk level, agent score, proposed action
- support approve/reject with note
- maintain audit trail

## Core components
- approval endpoint
- approval queue
- dashboard view model
- audit trail writer
- decision note handler

## Risks
- approval bottlenecks
- missing traceability
- inconsistent decision state
- rejected actions without follow-up path

## Acceptance criteria
- approval items are listed consistently
- approve/reject actions are persisted
- every decision has audit trail
- operator can see risk and proposed action
