from __future__ import annotations

from dataclasses import dataclass

from .message_models import MessagePlan, PlannedTask, ToolName


TASK_HINTS = {
    "покажи файлы": (ToolName.FS_LIST, "Пользователь хочет увидеть структуру папки"),
    "список файлов": (ToolName.FS_LIST, "Пользователь хочет увидеть структуру папки"),
    "прочитай файл": (ToolName.FS_READ, "Пользователь хочет прочитать конкретный файл"),
    "найди": (ToolName.FS_SEARCH, "Пользователь хочет найти текст или код"),
    "git status": (ToolName.GIT_STATUS, "Пользователь хочет статус репозитория"),
    "запусти python": (ToolName.RUN_PYTHON, "Пользователь хочет выполнить Python-код"),
    "выполни команду": (ToolName.RUN_SHELL, "Пользователь хочет выполнить shell-команду"),
    "проанализируй проект": (ToolName.AGENT_CALL, "Нужен агент анализа кода"),
}

GENERAL_QA_PREFIXES = (
    "что",
    "какие",
    "как",
    "почему",
    "зачем",
    "кто",
    "сколько",
)


@dataclass
class PlanConfig:
    project_root: str


class Planner:
    def __init__(self, config: PlanConfig) -> None:
        self.config = config

    def build(self, text: str) -> MessagePlan:
        normalized = " ".join(text.lower().strip().split())

        task = self._build_task_if_any(normalized, original=text)
        if task is None:
            return MessagePlan(
                mode="chat",
                reply_text=self._chat_reply(text),
                needs_worker=False,
                approval_required=False,
                tasks=[],
            )

        reply_text = self._hybrid_reply(text, task.tool.value)
        approval = task.tool.value == "run_shell"
        mode = "hybrid" if text.endswith("?") or any(normalized.startswith(p) for p in GENERAL_QA_PREFIXES) else "task"
        return MessagePlan(
            mode=mode,
            reply_text=reply_text,
            needs_worker=True,
            approval_required=approval,
            tasks=[task],
        )

    def _build_task_if_any(self, normalized: str, original: str) -> PlannedTask | None:
        for hint, (tool, reason) in TASK_HINTS.items():
            if hint in normalized:
                if tool == ToolName.FS_LIST:
                    return PlannedTask(tool=tool, reason=reason, args={"path": self.config.project_root})
                if tool == ToolName.GIT_STATUS:
                    return PlannedTask(tool=tool, reason=reason, args={"path": self.config.project_root})
                if tool == ToolName.FS_SEARCH:
                    query = self._extract_search_query(original)
                    return PlannedTask(
                        tool=tool,
                        reason=reason,
                        args={"path": self.config.project_root, "query": query},
                    )
                if tool == ToolName.FS_READ:
                    path = self._extract_path(original)
                    return PlannedTask(
                        tool=tool,
                        reason=reason,
                        args={"path": path or self.config.project_root},
                    )
                if tool == ToolName.RUN_SHELL:
                    command = self._extract_after_colon(original)
                    return PlannedTask(
                        tool=tool,
                        reason=reason,
                        args={"path": self.config.project_root, "command": command},
                    )
                if tool == ToolName.RUN_PYTHON:
                    code = self._extract_after_colon(original)
                    return PlannedTask(
                        tool=tool,
                        reason=reason,
                        args={"path": self.config.project_root, "code": code or "print('ok')"},
                    )
                if tool == ToolName.AGENT_CALL:
                    return PlannedTask(
                        tool=tool,
                        reason=reason,
                        args={"name": "code_analyst", "project_root": self.config.project_root, "question": original},
                    )
        return None

    def _chat_reply(self, text: str) -> str:
        lowered = text.lower().strip()
        if lowered.startswith("что ты умеешь"):
            return (
                "Я могу отвечать как обычный ИИ, анализировать проект, читать файлы в разрешённых папках, "
                "смотреть git status, искать по коду и при необходимости подключать внутренних агентов."
            )
        if lowered.startswith("что ты не умеешь"):
            return (
                "Я не должен без подтверждения делать опасные действия: удалять файлы, менять системные настройки, "
                "делать destructive git-команды или запускать сомнительные скрипты."
            )
        if "типы фундаментов" in lowered:
            return (
                "Основные типы фундаментов: ленточный, плитный, свайный, столбчатый и свайно-ростверковый. "
                "Выбор зависит от грунта, нагрузки, этажности и уровня грунтовых вод."
            )
        return (
            "Понял запрос. Я сначала отвечаю как обычный ИИ, а когда нужно — подключаю инструменты, "
            "локальные данные и других агентов."
        )

    def _hybrid_reply(self, text: str, tool: str) -> str:
        tool_to_text = {
            "fs_list": "Сейчас посмотрю структуру проекта.",
            "fs_read": "Сейчас прочитаю нужный файл.",
            "fs_search": "Сейчас поищу это по проекту.",
            "git_status": "Сейчас проверю состояние репозитория.",
            "run_python": "Сейчас выполню Python-код в безопасном режиме.",
            "run_shell": "Сейчас подготовлю выполнение команды. Для потенциально опасных действий понадобится подтверждение.",
            "agent_call": "Сейчас подключу внутреннего агента анализа проекта.",
        }
        return tool_to_text.get(tool, f"Принял запрос: {text}")

    @staticmethod
    def _extract_search_query(original: str) -> str:
        text = original.strip()
        if ":" in text:
            return text.split(":", 1)[1].strip() or "TODO"
        words = text.split()
        return words[-1] if words else "TODO"

    @staticmethod
    def _extract_after_colon(original: str) -> str:
        if ":" in original:
            return original.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _extract_path(original: str) -> str | None:
        for token in original.replace('"', '').split():
            if "\\" in token or token.endswith((".py", ".md", ".txt", ".json")):
                return token
        return None
