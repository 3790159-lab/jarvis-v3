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


# honesty_mode — ключ ВЕРХНЕГО уровня (в отличие от funnel_gate внутри telegram:).
# В шаблоне клиента он лежит закомментированным, поэтому тот же приём: живой или
# закомментированный ключ переписываем НА МЕСТЕ, чтобы не плодить второй.
# ВАЖНО: хвост после значения — только пробелы и/или комментарий. Без этого
# регексп цепляет строки ДОКУМЕНТАЦИИ вида
#   "# honesty_mode: honest (дефолт) — на «ты бот?» раскрывается честно. Снять"
# и переписывает прозу в живой ключ → loader падает на мусорном значении.
# (Поймано тестом: файл откатился, но команда не срабатывала.)
_HONESTY_RE = re.compile(
    r"^(?P<hash>#\s*)?honesty_mode\s*:\s*(?P<val>\S+)(?P<tail>[ \t]*(?:#.*)?)$")

HONESTY_HONEST = "honest"
HONESTY_FREE = "free_owner_liability"


def set_honesty_mode(text: str, *, honest: bool) -> str:
    """Вернуть settings.yaml с honesty_mode = honest | free_owner_liability.

    Значение пишем ПОЛНЫМ (`free_owner_liability`, не `free`): loader намеренно
    отвергает короткую форму, чтобы выключение честности нельзя было набрать
    мимоходом. Здесь та же причина — файл должен читаться как осознанный
    выбор с названной ответственностью, а не как флажок."""
    value = HONESTY_HONEST if honest else HONESTY_FREE
    lines = text.splitlines()

    # Живой ключ имеет приоритет над закомментированным: если в файле есть и
    # реальный `honesty_mode:`, и закомментированный образец из шаблона, править
    # надо тот, который действительно читает loader.
    matches = [(i, m) for i, m in ((i, _HONESTY_RE.match(l)) for i, l in enumerate(lines)) if m]
    live = [(i, m) for i, m in matches if not m.group("hash")]
    target = (live or matches)
    if target:
        i, m = target[0]
        lines[i] = f"honesty_mode: {value}{m.group('tail')}"
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    # Ключа нет вовсе — добавляем в конец: honesty_mode верхнеуровневый, привязки
    # к блоку у него нет, поэтому вставка безопасна в любом месте нулевого уровня.
    lines.append(f"honesty_mode: {value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
