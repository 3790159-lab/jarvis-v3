"""Цепочка зависимых DevTask (вариант C).

Спека: docs/superpowers/specs/2026-09-07-devtask-chain.md, ОК владельца 07.09.

Шаг цепочки — ОБЫЧНАЯ DevTask. Единственная разница: база worktree у шага N —
ветка шага N−1, а не `prod_head()`. Аргумент `base` у `create_worktree` уже
есть, поэтому в git-слое ничего не менялось.

Этот модуль — чистая логика плана: порядок, база, условия остановки, план
отката. Он НЕ мержит (сторож проверяет исходник литерально), НЕ удаляет и не
трогает git сам: снятие worktree и веток делает вызывающий по плану, который
здесь построен. Разделение намеренное — решение о снятии принимает человек,
а модуль только называет, что и в каком порядке снимать.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── потолки, утверждённые владельцем 07.09 ─────────────────────────────────
#
# 90 минут — замер по `state/dev_tasks/log.jsonl`: 82 задачи, время от
# `running` до `awaiting_review`, медиана 10 мин, 90-й перцентиль 18, максимум
# 160. Этот порог обрывает РОВНО ОДНУ задачу из 82; порог 120 обрывает ту же
# самую и ни одной больше.
#
# Денежного потолка здесь НЕТ намеренно: `devtask_auth_mode()` по умолчанию
# `subscription`, маргинальная стоимость шага ≈ $0, и потолок по деньгам
# ограничивал бы не тот ресурс. Дефицитен лимит плана — его ловит
# STOP_RATE_LIMIT.
MAX_STEPS = 10
STEP_TIMEOUT_S = 90 * 60
CHAIN_TIMEOUT_S = 5 * 60 * 60

# ── статусы цепочки ────────────────────────────────────────────────────────
CHAIN_RUNNING = "running"
CHAIN_STOPPED = "stopped"
CHAIN_DONE = "done"
CHAIN_ROLLED_BACK = "rolled_back"

# ── статусы шага ───────────────────────────────────────────────────────────
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_AWAITING_REVIEW = "awaiting_review"
STEP_MERGED = "merged"
STEP_ROLLED_BACK = "rolled_back"

#: Состояния, после которых шаг считается доделанным и пускает следующий.
_STEP_DONE_STATES = (STEP_AWAITING_REVIEW, STEP_MERGED)

# ── причины остановки (§5 спеки) ───────────────────────────────────────────
STOP_GATE_RED = "gate_red"
STOP_MERGE_CONFLICT = "merge_conflict"
STOP_STEP_TIMEOUT = "step_timeout"
STOP_CHAIN_TIMEOUT = "chain_timeout"
STOP_DELETION = "deletion_in_diff"
STOP_RATE_LIMIT = "rate_limited"
STOP_PREFLIGHT = "preflight_failed"
STOP_WORKTREE = "worktree_failed"
STOP_PROCESS_GONE = "process_gone"


class ChainError(RuntimeError):
    """Своя вершина иерархии: отказ цепочки не должен тонуть в широком except."""


class ChainClosedError(ChainError):
    """План объявляется целиком до старта; дописывать в цепочку нельзя."""


class ChainStateError(ChainError):
    """План внутренне противоречив — например, у шага N−1 нет задачи."""


class AlreadyMergedError(ChainError):
    """Откат ветки, уже уехавшей в транк, был бы враньём об откате."""


def branch_for_task(task_id: str) -> str:
    """Имя ветки задачи. Та же формула, что в `queue.add`."""
    return f"devtask-{task_id}"


class ChainStore:
    """Планы цепочек: файл на цепочку, рядом с карточками задач."""

    def __init__(self, base_dir: Any) -> None:
        self._base = Path(base_dir)

    # ── io ────────────────────────────────────────────────────────────────
    def _dir(self) -> Path:
        d = self._base / "chains"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, chain_id: str) -> Path:
        return self._dir() / f"{chain_id}.json"

    def _save(self, chain: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.block_l_common import save_json_safe
        save_json_safe(self._path(chain["id"]), chain)
        return chain

    def get(self, chain_id: str) -> Optional[Dict[str, Any]]:
        from app.services.block_l_common import load_json_safe
        path = self._path(chain_id)
        if not path.exists():
            return None
        return load_json_safe(path)

    def _require(self, chain_id: str) -> Dict[str, Any]:
        chain = self.get(chain_id)
        if chain is None:
            raise ChainStateError(f"цепочки {chain_id} нет")
        return chain

    # ── план ──────────────────────────────────────────────────────────────
    def create(self, title: str, descs: List[str],
               auto_advance: bool = True) -> Dict[str, Any]:
        """Объявить план ЦЕЛИКОМ. Пустой и переросший потолок — отказ."""
        descs = list(descs or [])
        if not descs:
            raise ValueError("цепочка без шагов не имеет смысла")
        if len(descs) > MAX_STEPS:
            raise ValueError(
                f"шагов {len(descs)}, потолок {MAX_STEPS}: длинную цепочку "
                "надо резать на несколько, иначе откат станет неразбираемым"
            )
        now = datetime.utcnow()
        chain_id = "chain_" + now.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
        chain = {
            "id": chain_id,
            "title": title,
            "created_at": now.isoformat(),
            "status": CHAIN_RUNNING,
            "auto_advance": bool(auto_advance),
            "stopped_reason": None,
            "steps": [
                {"step_no": i, "desc": d, "task_id": None, "status": STEP_PENDING}
                for i, d in enumerate(descs, 1)
            ],
        }
        return self._save(chain)

    def append_step(self, chain_id: str, desc: str) -> Dict[str, Any]:
        """Всегда отказ.

        План объявляется заранее и целиком. Иначе условие остановки
        «объявленный план исчерпан» перестаёт быть определённым: цепочка,
        в которую можно дописывать, не заканчивается никогда.
        """
        raise ChainClosedError(
            "план цепочки объявляется целиком до старта; "
            "нужен ещё шаг — заводи новую цепочку от ветки последнего шага"
        )

    def _step(self, chain: Dict[str, Any], step_no: int) -> Dict[str, Any]:
        for step in chain["steps"]:
            if step["step_no"] == step_no:
                return step
        raise ChainStateError(f"в цепочке {chain['id']} нет шага {step_no}")

    def attach_task(self, chain_id: str, step_no: int, task_id: str) -> Dict[str, Any]:
        chain = self._require(chain_id)
        step = self._step(chain, step_no)
        step["task_id"] = task_id
        step["status"] = STEP_RUNNING
        return self._save(chain)

    def set_step_status(self, chain_id: str, step_no: int, status: str) -> Dict[str, Any]:
        chain = self._require(chain_id)
        self._step(chain, step_no)["status"] = status
        return self._save(chain)

    def finish(self, chain_id: str) -> Dict[str, Any]:
        """Объявленный план пройден. Не мерж и не откат — просто конец плана."""
        chain = self._require(chain_id)
        chain["status"] = CHAIN_DONE
        chain["finished_at"] = datetime.utcnow().isoformat()
        return self._save(chain)

    def stop(self, chain_id: str, reason: str) -> Dict[str, Any]:
        """Остановка ничего не сносит: сделанное остаётся на диске нетронутым."""
        chain = self._require(chain_id)
        chain["status"] = CHAIN_STOPPED
        chain["stopped_reason"] = reason
        chain["stopped_at"] = datetime.utcnow().isoformat()
        return self._save(chain)


def base_for_step(chain: Dict[str, Any], step_no: int, prod_head: str) -> str:
    """База worktree шага: prod_head для первого, ветка предыдущего для остальных.

    Отсутствие задачи у предыдущего шага — это ДЫРА В ПЛАНЕ, а не повод молча
    взять prod_head: шаг N потерял бы код шага N−1, ради которого цепочка и
    заводилась, и обнаружилось бы это только человеком в отчёте.
    """
    if step_no <= 1:
        return prod_head
    prev = None
    for step in chain["steps"]:
        if step["step_no"] == step_no - 1:
            prev = step
            break
    if prev is None:
        raise ChainStateError(f"нет шага {step_no - 1}, не от чего ветвиться")
    if not prev.get("task_id"):
        raise ChainStateError(
            f"у шага {step_no - 1} нет задачи: база для шага {step_no} неизвестна"
        )
    return branch_for_task(prev["task_id"])


def next_step(chain: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Следующий шаг к запуску, или None.

    None означает три РАЗНЫЕ вещи, и вызывающий обязан их различать сам:
    цепочка остановлена, текущий шаг ещё идёт, план исчерпан
    (см. `is_exhausted`).
    """
    if chain.get("status") != CHAIN_RUNNING:
        return None
    for step in sorted(chain["steps"], key=lambda s: s["step_no"]):
        if step["status"] == STEP_PENDING:
            # Досюда доходим, только если ВСЕ предыдущие шаги доделаны: любой
            # недоделанный обрывает цикл проверкой ниже. Отдельная проверка
            # предшественника здесь была бы мёртвым кодом — мутационный гейт
            # её и поймал (мишень 3, первый прогон).
            return step
        if step["status"] not in _STEP_DONE_STATES:
            return None
    return None


def is_exhausted(chain: Dict[str, Any]) -> bool:
    """Объявленный план пройден: незапущенных шагов не осталось."""
    return all(s["status"] != STEP_PENDING for s in chain["steps"])


def stop_reason(verdict: Dict[str, Any]) -> Optional[str]:
    """Причина остановки цепочки, или None — «можно ехать дальше».

    Порядок проверок — это ПРИОРИТЕТ ПРИЧИН, а не оптимизация. Удаление стоит
    первым намеренно: про снос файлов владелец должен узнать поимённо, даже
    когда рядом есть другая причина. Дальше идёт инфраструктура (её отказ
    делает остальные вердикты недостоверными), потом код, потом время.
    """
    if verdict.get("has_deletions"):
        return STOP_DELETION
    if not verdict.get("worktree_ok", True):
        return STOP_WORKTREE
    if not verdict.get("preflight_ok", True):
        return STOP_PREFLIGHT
    if not verdict.get("process_alive", True):
        return STOP_PROCESS_GONE
    if verdict.get("rate_limited"):
        return STOP_RATE_LIMIT
    if verdict.get("merge_conflict"):
        return STOP_MERGE_CONFLICT
    if not verdict.get("gate_ok", True):
        return STOP_GATE_RED
    if float(verdict.get("step_elapsed_s") or 0) > STEP_TIMEOUT_S:
        return STOP_STEP_TIMEOUT
    if float(verdict.get("chain_elapsed_s") or 0) > CHAIN_TIMEOUT_S:
        return STOP_CHAIN_TIMEOUT
    return None


def rollback_plan(chain: Dict[str, Any], from_step: int,
                  is_merged: Callable[[str], bool]) -> List[Dict[str, Any]]:
    """Что снимать и в каком порядке — ОТ ПОСЛЕДНЕГО ШАГА К ПЕРВОМУ.

    Порядок не косметика: ветка шага N растёт из ветки N−1, и снятие с начала
    оставило бы висящие ветки.

    План называет ТОЛЬКО worktree и ветку. Отчёты, логи гейтов и журналы в него
    не попадают никогда: откат убирает код, но не улики, без которых разбор
    «почему цепочка пошла не туда» невозможен.

    Проверка `is_merged` идёт ДО построения плана и по ВСЕМ веткам: частично
    выполненный откат хуже несделанного.
    """
    steps = [s for s in sorted(chain["steps"], key=lambda s: s["step_no"])
             if s["step_no"] >= from_step and s.get("task_id")]
    for step in steps:
        branch = branch_for_task(step["task_id"])
        if is_merged(branch):
            raise AlreadyMergedError(
                f"ветка {branch} (шаг {step['step_no']}) уже в транке — "
                "снять её значило бы соврать об откате; разбирай руками"
            )
    return [
        {"step_no": s["step_no"], "task_id": s["task_id"],
         "branch": branch_for_task(s["task_id"])}
        for s in reversed(steps)
    ]
