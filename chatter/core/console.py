"""Пульт владельца: чистый разбор команд из Saved Messages (арка 3A).

Ноль Telethon и ноль сети: раннер приносит текст, получает Command."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

GLOBAL_COMMANDS = frozenset({"status", "stop", "start"})
TARGETED_COMMANDS = frozenset({"pause", "resume"})

_DURATION_RE = re.compile(r"^(\d+)([hm])$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    name: str
    duration_seconds: float | None = None
    target: str | None = None      # сырая ссылка/id, если владелец указал явно
    error: str | None = None       # человеческая жалоба вместо тихого игнора


def _parse_duration(token: str) -> float | None:
    m = _DURATION_RE.match(token)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    return value * (3600.0 if unit == "h" else 60.0)


def parse_command(text: str) -> Command | None:
    """None = это не команда (обычный текст в Saved Messages трогать нельзя)."""
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].casefold()
    args = parts[1:]

    if name in GLOBAL_COMMANDS:
        return Command(name=name)
    if name not in TARGETED_COMMANDS:
        return None

    duration: float | None = None
    if name == "pause" and args:
        duration = _parse_duration(args[0])
        if duration is not None:
            args = args[1:]
        elif not args[0].startswith(("t.me", "https://", "@")) and not args[0].isdigit():
            # Похоже на кривую длительность, а не на адресата. Сказать вслух:
            # молчаливый игнор = владелец уверен, что пауза стоит (DEV-18).
            return Command(name=name, error=f"не понял длительность: '{args[0]}' (примеры: 1h, 30m)")

    return Command(name=name, duration_seconds=duration, target=args[0] if args else None)


SOURCE_LABELS = {
    "human_takeover": "вы вмешались",
    "command": "команда /pause",
}


# ---------------------------------------------------------------------------
# Задача 1 под-арки 3A-UX (спека 2026-07-17-chatter-arc3a-ux-design.md §7):
# человеческие имена вместо голых id + HTML-экранирование + i18n.
# Чистые кирпичи — раннер (Telethon) достаёт first_name/last_name/title/
# username из entity и передаёт сюда голыми аргументами, сети здесь нет.
# ---------------------------------------------------------------------------

def display_name(
    *, first_name: str | None = None, last_name: str | None = None,
    title: str | None = None, username: str | None = None,
    user_id: int | str | None = None,
) -> str:
    """Имя для интерфейса, цепочка fallback из спеки §7:
    first_name+last_name -> title (каналы/группы) -> @username -> id.

    Голый числовой id — это признак того, что о человеке НЕ известно НИЧЕГО,
    а не нормальный вид карточки (находка живого дрила: оператор не смог
    возобновить диалог, увидев только число). Если известны и имя (или
    title), и username — оба идут в одну строку: "Даниил Лапин (@lapin)".

    Возвращаемая строка уже HTML-экранирована (escape_html) — она всегда
    подставляется в HTML-сообщение (кликабельное имя требует parse_mode),
    а first_name/last_name/title — ПОЛЬЗОВАТЕЛЬСКИЙ текст (человек может
    вписать себе в профиль что угодно, включая "<script>")."""
    full_name = " ".join(p for p in (first_name, last_name) if p)
    primary = full_name or title or None

    if primary:
        name = escape_html(primary)
        if username:
            name += f" (@{escape_html(username)})"
        return name
    if username:
        return f"@{escape_html(username)}"
    return escape_html(str(user_id))


def contact_link(*, username: str | None = None, user_id: int | str | None = None) -> str:
    """Кликабельная ссылка на диалог (спека §7): t.me/<username>, а если
    юзернейма нет — tg://user?id=<id> (работает, пока entity в кэше сессии).

    Username в Telegram ограничен алфавитом [A-Za-z0-9_] (сервер не
    позволяет ничего другого), поэтому здесь, в отличие от display_name,
    экранировать нечего — сюда не может попасть текст, который развалит
    HTML-разметку."""
    if username:
        return f"t.me/{username}"
    return f"tg://user?id={user_id}"


def escape_html(text: str) -> str:
    """Единая точка экранирования для ВСЕГО пользовательского текста,
    который подставляется в HTML-сообщение Telegram (parse_mode=HTML):
    имя, username, detail. html.escape(quote=True) закрывает все 5
    спецсимволов (<, >, &, ", '), не только <>& — Telegram-парсер такой же
    строгий к кавычкам внутри атрибутов, как браузер."""
    return html.escape(text, quote=True)


def safe_snippet(text: str, limit: int = 40) -> str:
    """Безопасный обрезанный фрагмент пользовательского текста (detail и
    т.п.) для HTML-сообщения. Порядок ОБЯЗАТЕЛЬНО такой:

        схлопнуть переводы строк -> обрезать СЫРОЙ текст -> экранировать

    а не "экранировать -> обрезать": html.escape раздувает один символ в
    многосимвольную сущность (& -> &amp;, 5 символов). Если резать ПОСЛЕ
    экранирования по количеству символов, срез может прийтись на середину
    сущности ("&amp;" -> "&am") — Telegram увидит незакрытую сущность и
    ОТКАЖЕТСЯ парсить HTML целиком, то есть /status перестанет отправляться
    вообще (это злее старого бага с \\n в detail — там ломалась только
    вёрстка). Экранирование ПОСЛЕ обрезки безопасно по построению: что бы
    ни осталось после среза сырого текста, escape() всегда выдаёт ЦЕЛУЮ
    сущность для каждого спецсимвола в остатке — оборванной сущности
    получиться не может в принципе."""
    collapsed = _one_line(text)
    truncated = collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"
    return escape_html(truncated)


# {язык: {смысловой_ключ: шаблон}} — по образцу disclosure.py (тот же
# паттерн словарь-на-язык с .get(language, ru-словарь) фолбэком), но там
# несколько узких словарей под разные части фразы, здесь один словарь под
# все строки пульта, потому что пульт — это много независимых сообщений,
# а не одна собираемая фраза. Ключи по СМЫСЛУ (status_header, resume_hint),
# не по русскому тексту — иначе переименование в одном языке шаталo бы все.
CONSOLE_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "status_header": "⏸ Паузы ({n}):",
        "status_intervened": "вы вмешались {gap} назад",
        "status_paused_for": "/pause {duration}, осталось {remaining}",
        "status_active_footer": "🟢 Бот активен · авто-возврат: прогон {gap} назад",
        "status_stopped_footer": "🔴 Бот остановлен (/stop). Снять: /start",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "список устарел, набери /status",
        "no_such_number": "нет такого номера, набери /status",
        "card_header": "⏸ Пауза: {name}",
        "card_intervened_detail": "Вы вмешались: «{detail}»",
        "card_silent": "{persona} молчит в этом диалоге.",
        "card_resume_reply_hint": "Ответьте /resume на это сообщение",
        "card_resume_status_hint": "или: /status → /resume <номер>",
    },
    "en": {
        "status_header": "⏸ Paused ({n}):",
        "status_intervened": "you stepped in {gap} ago",
        "status_paused_for": "/pause {duration}, {remaining} left",
        "status_active_footer": "🟢 Bot active · auto-resume: last run {gap} ago",
        "status_stopped_footer": "🔴 Bot stopped (/stop). Lift with: /start",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "list is stale, run /status",
        "no_such_number": "no such number, run /status",
        "card_header": "⏸ Paused: {name}",
        "card_intervened_detail": "You stepped in: «{detail}»",
        "card_silent": "{persona} is silent in this chat.",
        "card_resume_reply_hint": "Reply /resume to this message",
        "card_resume_status_hint": "or: /status → /resume <number>",
    },
    "uk": {
        "status_header": "⏸ Паузи ({n}):",
        "status_intervened": "ви втрутилися {gap} тому",
        "status_paused_for": "/pause {duration}, залишилось {remaining}",
        "status_active_footer": "🟢 Бот активний · авто-повернення: запуск {gap} тому",
        "status_stopped_footer": "🔴 Бот зупинено (/stop). Зняти: /start",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "список застарів, наберіть /status",
        "no_such_number": "немає такого номера, наберіть /status",
        "card_header": "⏸ Пауза: {name}",
        "card_intervened_detail": "Ви втрутилися: «{detail}»",
        "card_silent": "{persona} мовчить у цьому діалозі.",
        "card_resume_reply_hint": "Відповідайте /resume на це повідомлення",
        "card_resume_status_hint": "або: /status → /resume <номер>",
    },
}


def console_text(key: str, language: str = "ru", **kwargs) -> str:
    """Строка пульта на нужном языке (settings.yaml: language). Неизвестный
    язык -> ru, тот же фолбэк, что disclosure.honest_disclosure — владелец
    всегда получит понятный текст, даже если раннер передал опечатку/новый
    язык, которого ещё нет в словаре, вместо KeyError на ровном месте."""
    strings = CONSOLE_STRINGS.get(language, CONSOLE_STRINGS["ru"])
    template = strings[key]
    return template.format(**kwargs) if kwargs else template


@dataclass(frozen=True)
class PauseView:
    """Готовая к печати строка о паузе. Раннер разрешает имя/ссылку (это
    Telethon), форматтер остаётся чистым."""
    title: str
    link: str
    since_ts: float
    source: str
    detail: str | None
    msg_id: int | None
    resume_eta_ts: float | None


def _hhmm(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _one_line(text: str) -> str:
    """Схлопывает переводы строк в пробел.

    p.detail — дословный текст ВЛАДЕЛЬЦА (Shift+Enter в Telegram даёт
    многострочное сообщение, это обычный сценарий, не эксплойт), p.title —
    тоже произвольная строка. Если пропустить \n как есть в lines.append(...),
    join("\n") печатает внедрённый перевод строки как отдельную "физическую"
    строку статуса — без отступа, неотличимую от настоящей (вторая половина
    может даже начаться с "• " и выглядеть как ещё один заглушённый диалог).
    /status обязан быть тем, чему владелец может доверять не глядя — поэтому
    схлопываем ДО обрезки [:40], иначе \n съедает лимит длины."""
    return " ".join(text.split())


def _humanize_gap(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}с"
    if seconds < 3600:
        return f"{int(seconds // 60)}м"
    return f"{int(seconds // 3600)}ч {int((seconds % 3600) // 60)}м"


def format_status(
    *, kill_switch: bool, pauses: list[PauseView], counters: dict[str, int],
    autoresume_beat_age: float | None, autoresume_interval: float,
    now: float, window_hours: int,
) -> str:
    """Ответ на «почему Аня молчит» за 5 секунд, а не расследованием."""
    lines = ["🤖 Аня — статус"]
    if kill_switch:
        lines.append("Глобально: 🔴 ЗАГЛУШЕНА (/stop). Снять: /start")
    else:
        lines.append("Глобально: РАБОТАЕТ")

    lines.append(f"Заглушено диалогов: {len(pauses)}")
    for p in pauses:
        title = _one_line(p.title)
        lines.append(f" • {title} — {p.link} — с {_hhmm(p.since_ts)}")
        why = SOURCE_LABELS.get(p.source, p.source)
        if p.detail:
            detail = _one_line(p.detail)
            snippet = detail if len(detail) <= 40 else detail[:40] + "…"
            why += f" («{snippet}»"
            why += f", msg {p.msg_id})" if p.msg_id else ")"
        lines.append(f"   причина: {why}")
        if p.resume_eta_ts is not None:
            lines.append(f"   авто-возврат через {_humanize_gap(p.resume_eta_ts - now)}")
        else:
            lines.append("   авто-возврата нет — снимет только /resume")

    c = counters
    lines.append(
        f"За {window_hours}ч: перехватов {c.get('takeover', 0)} · "
        f"неатрибутированных пауз {c.get('unattributed_pause', 0)} · "
        f"неопознанных исходящих {c.get('unknown_outgoing', 0)}")

    # Кто сторожит сторожа: мёртвая задача авто-возврата неотличима от
    # «пауз к возврату нет», если не показать возраст её heartbeat. Отдельно
    # ловим beat_age=None (задача НИ РАЗУ не отработала — раннер только что
    # стартовал, или задача умерла ещё до первого прогона): сравнение None с
    # числом упало бы TypeError, а молчаливый пропуск проверки скрыл бы ровно
    # тот случай, который эта строка обязана заметить (DEV-18).
    if autoresume_beat_age is None:
        lines.append("⚠️ Авто-возврат: НИ РАЗУ не отработал")
    elif autoresume_beat_age > 3 * autoresume_interval:
        lines.append(f"⚠️ Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    else:
        lines.append(f"Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    return "\n".join(lines)
