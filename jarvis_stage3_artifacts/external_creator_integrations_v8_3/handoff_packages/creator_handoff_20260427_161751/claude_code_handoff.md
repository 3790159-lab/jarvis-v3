# Claude Code Handoff — Jarvis

## Goal
Improve Jarvis Operator Dashboard and backend autonomy safely.

## Current architecture
- FastAPI backend
- Jarvis tool gateway
- Night Mode
- Evidence / Truth Guard
- Auto Task Generator
- n8n workflow artifact creator
- FULL CREATOR product package

## Safe tasks for Claude Code
1. Add read-only dashboard API endpoints.
2. Add artifact index endpoint.
3. Add queue status endpoint.
4. Add latest evidence endpoint.
5. Add tests for those endpoints.
6. Avoid destructive actions.

## Rules
- Do not print secrets.
- Do not overwrite env files.
- Do not push to GitHub automatically.
- Create patches and reports first.
