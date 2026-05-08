# ENDPOINTS_LIVE.md — Все живые endpoints

Источник: `GET http://127.0.0.1:8015/openapi.json`  
Дата: 2026-04-30  
**Итого: 321 endpoint**

---

## Корневые / служебные (untagged)

```
GET      /
GET      /_jarvis/router/status
GET      /health
GET      /healthz
GET      /dashboard
GET      /dashboard/data
GET      /dashboard/missions/{mission_id}
GET      /policy
POST     /respond
POST     /routes/debug
```

## Memory (legacy, untagged)

```
GET      /memory/stats
GET      /memory/{mission_id}
POST     /memory/{mission_id}/compress
GET      /memory/{mission_id}/context
```

## Missions (legacy, untagged)

```
GET      /missions
GET      /missions/{mission_id}
POST     /missions/{mission_id}/cancel
GET      /missions/{mission_id}/memory
GET      /missions/{mission_id}/queue
POST     /missions/{mission_id}/retry
```

## Worker (untagged)

```
POST     /worker/recover-stale
POST     /worker/resume-missions
POST     /worker/run-once
GET      /worker/status
```

## AI Router [ai-router]

```
POST     /api/ai/dispatch
GET      /api/ai/health
```

## Agent Control Plane [agent_control_plane]

```
GET      /api/agents
GET      /api/agents/adapters
POST     /api/agents/invoke
GET      /api/agents/registry
GET      /api/agents/tasks
POST     /api/agents/tasks
POST     /api/agents/tasks/lease
POST     /api/agents/tasks/{task_id}/complete
POST     /api/agents/tasks/{task_id}/fail
GET      /api/approvals
POST     /api/approvals/request
POST     /api/approvals/{approval_id}/approve
POST     /api/approvals/{approval_id}/reject
GET      /api/control/health
POST     /api/memory/ingest-mission/{mission_id}
GET      /api/memory/recent
POST     /api/memory/remember
GET      /api/memory/search
```

## Agent Mesh [agent-mesh, agent-mesh-plus, agent-mesh-stability, agent-mesh-real-exec, agent-mesh-improvement]

```
GET      /api/agent-mesh/health
GET      /api/agent-mesh/adaptive-policy
GET      /api/agent-mesh/auth/health
GET      /api/agent-mesh/connector-policy/{agent_id}/{capability}
GET      /api/agent-mesh/connectors/log
POST     /api/agent-mesh/demo-run-v10
GET      /api/agent-mesh/learning/health
POST     /api/agent-mesh/learning/rebuild
GET      /api/agent-mesh/learning/variants
POST     /api/agent-mesh/learning/variants/propose
POST     /api/agent-mesh/learning/variants/approve/{variant_id}
POST     /api/agent-mesh/learning/variants/apply/{variant_id}
GET      /api/agent-mesh/learning/experiments/runs
POST     /api/agent-mesh/learning/experiments/run
GET      /api/agent-mesh/learning/recommendation
GET      /api/agent-mesh/learning/skills
GET      /api/agent-mesh/learning/lessons
POST     /api/agent-mesh/learning/guidance/preview
POST     /api/agent-mesh/runtime/connectors/execute
POST     /api/agent-mesh/runtime/integration-task/execute
GET      /api/agent-mesh/strategy/active
GET      /api/agent-mesh/missions/{mission_id}
GET      /api/agent-mesh/lifecycle/health
POST     /api/agent-mesh/lifecycle/cleanup
POST     /api/agent-mesh/knowledge/ingest
GET      /api/agent-mesh/knowledge/library
GET      /api/agent-mesh/knowledge/domains
GET      /api/agent-mesh/knowledge/review-queue
POST     /api/agent-mesh/knowledge/review/approve/{review_id}
POST     /api/agent-mesh/knowledge/review/reject/{review_id}
GET      /api/agent-mesh/knowledge/negative-rules
GET      /api/agent-mesh/service-traces
GET      /api/agent-mesh/service-telemetry
GET      /api/agent-mesh/communication-policy
GET      /api/agent-mesh/autonomy/health
GET      /api/agent-mesh/autonomy/history
POST     /api/agent-mesh/autonomy/tick
POST     /api/agent-mesh/autonomy/enable
POST     /api/agent-mesh/autonomy/disable
GET      /api/agent-mesh/self-healing/health
POST     /api/agent-mesh/self-healing/tick
GET      /api/agent-mesh/service-guard/health
GET      /api/agent-mesh/service-resolution/example
GET      /api/agent-mesh/n8n/health
POST     /api/agent-mesh/n8n/verify
POST     /api/agent-mesh/n8n/test-webhook
POST     /api/agent-mesh/n8n/promote-live
POST     /api/agent-mesh/traces/rebuild
GET      /api/agent-mesh/real-exec/health
GET      /api/agent-mesh/improvement/health
GET      /api/agent-mesh/improvement/proposals
POST     /api/agent-mesh/improvement/tick
GET      /api/agent-mesh/improvement/registry
POST     /api/agent-mesh/improvement/continuous/start
POST     /api/agent-mesh/improvement/continuous/stop
GET      /api/agent-mesh/improvement/continuous/status
GET      /api/agent-mesh/improvement/journal
GET      /api/agent-mesh/claude-probe/health
```

## Autonomy [autonomy, autonomy-bridge]

```
GET      /api/autonomy/health
GET      /api/autonomy/dashboard
GET      /api/autonomy/chains
POST     /api/autonomy/chains/execute
GET      /api/autonomy/engine/executions
POST     /api/autonomy/execute-step
GET      /api/autonomy/graphs
POST     /api/autonomy/graphs/execute
GET      /api/autonomy/graphs/executions
POST     /api/autonomy/memory/snapshot/{mission_id}
GET      /api/autonomy/mission-registry
GET      /api/autonomy/mission-templates
GET      /api/autonomy/missions
POST     /api/autonomy/missions
GET      /api/autonomy/missions/{mission_id}
GET      /api/autonomy/missions/{mission_id}/history
POST     /api/autonomy/missions/{mission_id}/run
POST     /api/autonomy/missions/multistep/execute
POST     /api/autonomy/missions/snapshot
POST     /api/autonomy/missions/{mission_id}/recover
POST     /api/autonomy/missions/{mission_id}/resume
POST     /api/autonomy/multi-step/execute
GET      /api/autonomy/operator-escalations
POST     /api/autonomy/reconcile/runtime
GET      /api/autonomy/recovery-policy
POST     /api/autonomy/recovery-policy
GET      /api/autonomy/repairs
POST     /api/autonomy/repairs/execute
GET      /api/autonomy/repairs/executions
GET      /api/autonomy/resume/health
GET      /api/autonomy/resume/snapshots
GET      /api/autonomy/resume/stale
POST     /api/autonomy/resume/stale/mark
GET      /api/autonomy/tools
POST     /api/autonomy/tools/execute
POST     /api/autonomy/tools/execute-plan
GET      /api/autonomy/tools/executions
GET      /api/autonomy/workflows
POST     /api/autonomy/workflows/execute
GET      /api/autonomy/workflows/executions
POST     /api/autonomy/archive/jobs
POST     /api/autonomy/archive/missions
GET      /api/autonomy/mission-bridge/check/{mission_id}
GET      /api/autonomy/mission-bridge/health
POST     /api/autonomy/mission-bridge/run/{mission_id}
```

## Cloud Control [cloud_control]

```
GET      /api/cloud/health
GET      /api/cloud/agents
GET      /api/cloud/audit/recent
GET      /api/cloud/capabilities
POST     /api/cloud/execute
GET      /api/cloud/operator-mode
POST     /api/cloud/plan-and-execute
POST     /api/cloud/raw-invoke
GET      /api/cloud/tools
```

## Goals / Missions API

```
GET      /api/goals                            [compat_legacy]
POST     /api/goals                            [compat_legacy]
GET      /api/missions                         [goals]
GET      /api/missions/{mission_id}            [goals]
GET      /api/missions/{mission_id}/logs       [goals]
POST     /api/missions/{mission_id}/run        [missions]
POST     /api/missions/multi-step/execute      [multistep]
POST     /api/missions/multistep/execute       [multistep]
POST     /api/multi-step/execute               [multi-step-execution]
```

## Jarvis Brain / Identity

```
GET      /api/jarvis/brain/health              [jarvis-brain]
POST     /api/jarvis/brain/plan                [jarvis-brain]
POST     /api/jarvis/brain/think               [jarvis-brain]
GET      /api/jarvis/identity                  [Jarvis Brain V2]
GET      /api/jarvis/self-check                [Jarvis Brain V2]
GET      /api/brain-executor/health            [jarvis-brain-executor]
GET      /api/brain-executor/latest            [jarvis-brain-executor]
POST     /api/brain-executor/run               [jarvis-brain-executor]
POST     /api/brain-v2/respond                 [Jarvis Multi-AI Orchestrator V1]
GET      /api/multi-ai/health                  [Jarvis Multi-AI Orchestrator V1]
POST     /api/multi-ai/respond                 [Jarvis Multi-AI Orchestrator V1]
```

## Jarvis Live Operator

```
POST     /api/jarvis/live-command              [jarvis-live-operator]
GET      /api/jarvis/live-health               [jarvis-live-operator]
POST     /api/jarvis/respond-live              [jarvis-live-operator]
```

## Jarvis Tools / Internet / AI Engineer

```
POST     /api/jarvis/tools/internet/compare-services   [jarvis-internet-tools]
POST     /api/jarvis/tools/internet/engineer-brief     [jarvis-internet-tools]
POST     /api/jarvis/tools/internet/find-pipelines     [jarvis-internet-tools]
GET      /api/jarvis/tools/internet/health             [jarvis-internet-tools]
POST     /api/jarvis/tools/internet/research           [jarvis-internet-tools]
POST     /api/jarvis/tools/internet/run-tool           [jarvis-internet-tools]
POST     /api/jarvis/tools/internet/search             [jarvis-internet-tools]
GET      /api/jarvis/ai-engineer/health                [jarvis-ai-engineer]
POST     /api/jarvis/ai-engineer/recommend-provider    [jarvis-ai-engineer]
POST     /api/jarvis/ai-engineer/review                [jarvis-ai-engineer]
GET      /api/jarvis/telegram-tools/health             [jarvis-telegram-tools]
POST     /api/jarvis/telegram-tools/internet-table     [jarvis-telegram-tools]
```

## n8n Роутеры

```
GET      /api/n8n/health                                     [n8n_bridge]
POST     /api/n8n/dispatch                                   [n8n_bridge]
GET      /api/n8n/materializer/health                        [n8n-materializer]
GET      /api/n8n/materializer/debug-state                   [n8n-materializer]
POST     /api/n8n/materializer/dry-run-payload               [n8n-materializer]
POST     /api/n8n/materializer/execute                       [n8n-materializer]
POST     /api/n8n/materializer/publish-workflow              [n8n-materializer]
POST     /api/n8n/materializer/publish-and-probe             [n8n-materializer]
GET      /api/n8n/materializer-v2/health                     [n8n-materializer-v2]
GET      /api/n8n/materializer-v2/debug-state                [n8n-materializer-v2]
POST     /api/n8n/materializer-v2/dry-run-payload            [n8n-materializer-v2]
POST     /api/n8n/materializer-v2/create-only                [n8n-materializer-v2]
POST     /api/n8n/materializer-v2/publish-workflow           [n8n-materializer-v2]
POST     /api/n8n/materializer-v2/publish-and-probe          [n8n-materializer-v2]
POST     /api/n8n/materializer-v2/execute                    [n8n-materializer-v2]
GET      /api/jarvis/n8n/health                              [jarvis-n8n]
GET      /api/jarvis/n8n/config                              [jarvis-n8n]
GET      /api/jarvis/n8n/public-api-check                    [jarvis-n8n]
GET      /api/jarvis/n8n/workflows                           [jarvis-n8n]
POST     /api/jarvis/n8n/workflows/create-webhook            [jarvis-n8n]
POST     /api/jarvis/n8n/workflows/activate/{workflow_id}    [jarvis-n8n]
POST     /api/jarvis/n8n/probe/{webhook_path}                [jarvis-n8n]
POST     /api/jarvis/n8n/smoke/local-webhook                 [jarvis-n8n]
GET      /api/supervisor/canvas/health                       [n8n_canvas_builder]
POST     /api/supervisor/canvas/build                        [n8n_canvas_builder]
GET      /api/supervisor/materializer/health                 [n8n_workflow_materializer]
POST     /api/supervisor/materializer/run                    [n8n_workflow_materializer]
GET      /api/n8n-specialist/health                          [n8n-specialist]
GET      /api/n8n-specialist/latest                          [n8n-specialist]
POST     /api/n8n-specialist/run                             [n8n-specialist]
GET      /api/n8n-super-agent/health                         [n8n-super-agent]
GET      /api/n8n-super-agent/latest                         [n8n-super-agent]
POST     /api/n8n-super-agent/run                            [n8n-super-agent]
```

## Memory Layer / Mission Memory

```
GET      /api/memory-layer/list/{category}       [memory-layer]
POST     /api/memory-layer/mission-summary       [memory-layer]
POST     /api/memory-layer/write                 [memory-layer]
POST     /api/mission-memory/run                 [mission-memory]
POST     /api/mission-run-memory/write           [mission-run-memory]
```

## Mission Graph / Artifacts / AI

```
GET      /api/mission-graph/health
POST     /api/mission-graph/submit
GET      /api/mission-graph/runs
GET      /api/mission-graph/missions/{graph_mission_id}
POST     /api/mission-graph/missions/{graph_mission_id}/cancel
POST     /api/mission-graph/missions/{graph_mission_id}/execute
POST     /api/mission-graph/missions/{graph_mission_id}/package
POST     /api/mission-graph/missions/{graph_mission_id}/rerun
POST     /api/mission-graph/missions/{graph_mission_id}/steps/{step_id}/retry
POST     /api/mission-artifacts/classify
POST     /api/mission-artifacts/execute
GET      /api/mission-artifacts/health
POST     /api/mission-ai/classify-task
POST     /api/mission-ai/execute
POST     /api/artifacts/build
GET      /api/artifacts/health
GET      /api/artifacts/missions
GET      /api/artifacts/missions/{mission_id}/manifest
GET      /api/artifacts/missions/{mission_id}/result
```

## Прочее

```
GET      /api/google/health
POST     /api/google/execute
POST     /api/obsidian/export-note
POST     /api/obsidian/auto-mission-memory
POST     /api/external-executor/execute
GET      /api/external-executor/health
POST     /api/execution-planner/plan
GET      /api/provider-routing/health
POST     /api/provider-routing/decide
POST     /api/respond                              [compat_legacy]
GET      /api/agents                               [compat_legacy]
GET      /api/goals                                [compat_legacy]
POST     /api/goals                                [compat_legacy]
GET      /api/supervisor/automation/health
POST     /api/supervisor/automation/preview
POST     /api/supervisor/automation/run
GET      /api/supervisor/pipeline/health
POST     /api/supervisor/pipeline/preview
POST     /api/supervisor/pipeline/run
GET      /api/operator/health
GET      /api/operator/next
GET      /api/operator/status
GET      /api/operator/tasks
POST     /api/operator/tasks
GET      /api/operator/tasks/{task_id}
PATCH    /api/operator/tasks/{task_id}
POST     /api/operator/telegram/report
GET      /api/operator-dashboard/health
GET      /api/operator-dashboard/summary
GET      /api/operator-dashboard/evidence
GET      /api/operator-dashboard/full-creator
GET      /api/operator-dashboard/gateway
GET      /api/operator-dashboard/queue
GET      /api/operator-dashboard/self-healing
GET      /api/unified-night/health
GET      /api/unified-night/latest
GET      /api/unified-night/metrics
POST     /api/unified-night/run
GET      /api/time-brain/now
GET      /api/time-brain/scheduled
POST     /api/time-brain/schedule
POST     /api/time-brain/dispatch-due
GET      /api/tools/health
GET      /api/tools/registry
POST     /api/tools/execute
GET      /api/tools/router/health
POST     /api/tools/router/plan
GET      /api/tools/chains/health
POST     /api/tools/chains/plan
GET      /api/runtime-bridge/health
POST     /api/runtime-bridge/submit
GET      /api/runtime-bridge/runs
GET      /api/runtime-bridge/goals/{goal_id}
GET      /api/runtime-bridge/missions/{mission_id}
POST     /api/runtime-bridge/missions/{mission_id}/cancel
POST     /api/runtime-bridge/missions/{mission_id}/execute
GET      /api/jarvis/v5/content-factory/async-health
GET      /api/jarvis/v5/content-factory/jobs/{job_id}
POST     /api/jarvis/v5/content-factory/submit
POST     /api/specialized_agents/coding
POST     /api/agents/reasoning
POST     /api/agents/research
```

---

**TOTAL: 321 endpoints**
