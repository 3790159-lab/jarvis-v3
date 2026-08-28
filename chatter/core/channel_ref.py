# -*- coding: utf-8 -*-
"""КАНАЛ контакта — переходный шов пары D к формату пары C.

🔴 ЭТОТ МОДУЛЬ ОБЯЗАН ИСЧЕЗНУТЬ. Он существует ровно потому, что пара D
(доставка перестаёт быть телеграмной) пишется ПРОТИВ контракта пары C
(`"<channel>:<external_id>:<persona>"`), но мержится после неё и от её кода не
зависит. В день, когда `chatter/core/contact_ref.py` научится трёхсегментной
форме и заведёт `channel_of`, каждая функция здесь становится однострочным
делегатом, и модуль сводится к `from chatter.core.contact_ref import *`.

ПОЧЕМУ НЕ ПРАВКА `contact_ref` ПРЯМО ЗДЕСЬ. Файл — предмет параллельной арки:
две арки, правящие один модуль по одной и той же причине, дают конфликт
мержа, в котором «кто последний, тот и прав», а второй разбор появляется
молча. Поэтому шов вынесен и НАЗВАН, а не спрятан внутри доставки.

ПОЧЕМУ РАЗБОР ВСЁ-ТАКИ ОДИН. Функции ниже НЕ угадывают: они СНАЧАЛА
спрашивают `contact_ref` (владельца ответа) и переходят к собственному разбору
только тогда, когда владелец этой формы ещё не знает. То есть после мержа пары
C переходная ветка становится недостижимой, а до него она — единственная, и
второй разбор на дерево не заводится ни в один момент времени.

FAIL-CLOSED, КАК У ВЛАДЕЛЬЦА. Форму, которой не знаем, не угадываем: тот же
`ContactRefError`, что и у `contact_ref`, и с тем же правилом — сообщение
несёт ВЕСЬ `contact_id`, а не голову.
"""
from __future__ import annotations

from chatter.core import contact_ref
from chatter.core.contact_ref import SEPARATOR, ContactRefError

# Голова телеграмного контакта в формате пары C — СЛОВОМ, а не `tg`
# (спека C §3.1). Здесь она нужна ровно за тем, чтобы сегодняшняя
# двухсегментная форма получила ИМЯ канала, которого у неё в строке нет.
TELEGRAM = "telegram"

# Сколько сегментов у формы С КАНАЛОМ и у сегодняшней. Литералами, а не
# выведенными из чего-нибудь ([[jarvis-literal-lists-not-introspection]]).
SEGMENTS_WITH_CHANNEL = 3
SEGMENTS_LEGACY = 2


def _transitional(contact_id) -> tuple[str, str, str]:
    """(канал, собеседник, слуг) для ОБЕИХ форм. Переходная ветка.

    Трёхсегментная форма читается ровно так, как её описывает спека C §3.1:
    голова — канал. Двухсегментная (сегодняшняя, вся живая база) канала в
    строке не несёт вовсе, и её канал — телеграм: третьего варианта в дереве
    нет и до волны 3 не будет.
    """
    if not isinstance(contact_id, str):
        raise ContactRefError(
            "contact_id обязан быть строкой, пришло %r (%s)"
            % (contact_id, type(contact_id).__name__))
    parts = contact_id.split(SEPARATOR)
    if len(parts) == SEGMENTS_WITH_CHANNEL and all(parts):
        return parts[0], parts[1], parts[2]
    if len(parts) == SEGMENTS_LEGACY and all(parts):
        return TELEGRAM, parts[0], parts[1]
    raise ContactRefError(
        "contact_id %r: ждём <канал>%s<собеседник>%s<персона> (%d сегмента) "
        "либо сегодняшнюю форму <собеседник>%s<персона> (%d), получили %d"
        % (contact_id, SEPARATOR, SEPARATOR, SEGMENTS_WITH_CHANNEL,
           SEPARATOR, SEGMENTS_LEGACY, len(parts)))


def channel_of(contact_id: str) -> str:
    """КАНАЛ, которым этот контакт адресуется.

    Владелец ответа — `contact_ref.channel_of` (пара C). Пока его в дереве
    нет, отвечает переходная ветка.
    """
    upstream = getattr(contact_ref, "channel_of", None)
    if upstream is not None:
        return upstream(contact_id)
    return _transitional(contact_id)[0]


def external_of(contact_id: str) -> str:
    """СОБЕСЕДНИК внутри канала (`external_id` спеки C §3.3).

    `contact_ref.peer_of` на форме с каналом отдаёт именно его — это и есть
    инвариант, ради которого писалась пара A. Пока форма ему незнакома,
    отвечает переходная ветка.
    """
    try:
        return contact_ref.peer_of(contact_id)
    except ContactRefError:
        return _transitional(contact_id)[1]


def slug_of(contact_id: str) -> str:
    """СЛУГ персоны — хвост в обеих формах."""
    try:
        return contact_ref.slug_of(contact_id)
    except ContactRefError:
        return _transitional(contact_id)[2]
