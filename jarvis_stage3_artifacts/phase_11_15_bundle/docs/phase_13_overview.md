# Phase 13 — Agent Performance Feedback Loop

## Purpose
Measure agent quality using real execution outcomes and historical performance.

## Main goals
- score successful and failed stage execution
- track per-role KPIs
- keep historical performance
- improve auto-disable and quarantine logic
- support downgrade / upgrade decisions

## Core components
- scoring engine
- KPI registry
- historical performance store
- policy hooks for downgrade/quarantine
- decision logic evaluator

## Risks
- weak scoring signal
- over-quarantine of useful agents
- stale performance history
- noisy metrics

## Acceptance criteria
- agents receive persistent performance score
- role-based KPIs are stored
- bad performance influences routing decisions
- scoring history is queryable
