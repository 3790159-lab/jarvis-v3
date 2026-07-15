# CC Session Context

Snapshot of a long-running Claude Code session's state, so a new session (or
a reconnect after an SSH drop) can resume without re-deriving everything from
scratch. Lives at `state/cc_session_context.md` (gitignored - it's live
session state, not repo content). CC overwrites this file at the end of every
крупный шаг (see CLAUDE.md, "Живучесть CC-сессий").

Copy this template to `state/cc_session_context.md` and fill in every
section - do not leave a section silently empty; write "-" or "нет" if there
is genuinely nothing to report.

---

## Обновлено
<!-- ISO timestamp of the last time this file was written -->

## Арка
<!-- Which arc/stage of docs/MASTER-PLAN.md (or which task) this session is working -->

## Сделано
<!-- Bullet list of concrete completed steps this session, most recent first -->
-

## Ждёт
<!-- What the session is currently blocked on or about to do next -->
-

## Открытые confirm
<!-- Any pending confirmations shown to Daniil that have not been tapped yet -->
-

## iteration_counter
<!-- One line per "ручка" (lever/problem/feature) currently being iterated on,
     e.g. `infra_restart_cloudflared: 1`. Reset a lever's counter to 0 only
     after it passes acceptance. At 2, the stop-limit rule in CLAUDE.md
     applies: pause and escalate to Daniil instead of a 3rd silent attempt. -->
-
