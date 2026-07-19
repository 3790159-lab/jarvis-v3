"""Точечная правка settings.yaml с СОХРАНЕНИЕМ комментариев.

Почему текстом, а не yaml.safe_dump: settings.yaml — половина документации
продукта (почему такой allowlist, что значит forbidden_terms, когда включать
funnel_gate). safe_dump выкинул бы все комментарии и переупорядочил ключи —
клиент открыл бы файл и увидел голый машинный дамп вместо инструкции.

Ноль сети, ноль состояния — чистые строковые функции."""
from __future__ import annotations

import re


class YamlEditError(Exception):
    pass


_TELEGRAM_BLOCK_RE = re.compile(r"^telegram:\s*$")
# Ключ может быть живым ("  funnel_gate: false") или закомментированным
# ("  # funnel_gate: true   # ← подтверждение") — в demo он именно закомментирован.
_GATE_RE = re.compile(r"^(?P<indent>\s+)(?P<hash>#\s*)?funnel_gate\s*:\s*(?P<val>\S+)(?P<tail>.*)$")


def _is_top_level_key(line: str) -> bool:
    return bool(line.strip()) and not line[0].isspace() and not line.lstrip().startswith("#")


def set_funnel_gate(text: str, enabled: bool) -> str:
    """Вернуть settings.yaml с telegram.funnel_gate = enabled.

    Существующий ключ (в т.ч. закомментированный) — переписываем на месте,
    чтобы не плодить второй. Отсутствующий — вставляем в конец блока telegram.
    Нет блока telegram — ошибка: гейт без блока бессмыслен (некому задавать
    allowlist), и молча создавать его мы не будем."""
    lines = text.splitlines()
    value = "true" if enabled else "false"

    start = next((i for i, l in enumerate(lines) if _TELEGRAM_BLOCK_RE.match(l)), None)
    if start is None:
        raise YamlEditError("в settings.yaml нет блока 'telegram:'")

    # Границы блока: до следующего ключа нулевого уровня.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _is_top_level_key(lines[i]):
            end = i
            break

    indent = "  "
    last_content = start          # куда вставлять, если ключа нет
    for i in range(start + 1, end):
        m = _GATE_RE.match(lines[i])
        if m:
            # Комментарий-пояснение справа сохраняем: он объясняет ЗАЧЕМ флаг.
            tail = m.group("tail") if not m.group("hash") else ""
            lines[i] = f"{m.group('indent')}funnel_gate: {value}{tail}"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            last_content = i

    lines.insert(last_content + 1, f"{indent}funnel_gate: {value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
