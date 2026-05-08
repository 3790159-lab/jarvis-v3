# Phase 12 — Tool Adapters Hardening

## Purpose
Make tool execution safe, bounded and structured.

## Main goals
- introduce a normal tool gateway
- define allowlist for shell / HTTP / filesystem
- enforce timeout budget
- enforce filesystem boundaries
- normalize tool output format

## Core components
- tool gateway
- allowlist validator
- timeout budget manager
- filesystem guard
- structured tool result adapter

## Risks
- unrestricted shell execution
- path traversal
- hanging tool calls
- inconsistent tool outputs

## Acceptance criteria
- all tool calls go through gateway
- allowlist is enforced
- filesystem stays inside approved roots
- every tool returns structured result
