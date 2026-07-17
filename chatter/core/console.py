"""Пульт владельца: чистый разбор команд из Saved Messages (арка 3A).

Ноль Telethon и ноль сети: раннер приносит текст, получает Command."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

GLOBAL_COMMANDS = frozenset({"status", "stop", "start", "help"})
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
            # escape_html(args[0]): это СЫРОЙ токен, который владелец только
            # что вписал руками -- с переходом всего пульта на parse_mode=html
            # (задача 3 под-арки 3A-UX) непроэкранированный '<' в нём развалил
            # бы разметку и Telegram отказался бы отправлять ответ целиком
            # (та же ловушка, что и с detail, просто источник другой команда).
            return Command(name=name, error=f"не понял длительность: '{escape_html(args[0])}' (примеры: 1h, 30m)")

    return Command(name=name, duration_seconds=duration, target=args[0] if args else None)


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
        "status_paused_indefinite": "/pause бессрочно",
        "status_quote": "«{detail}»",
        "status_window_counters": (
            "За {window_hours}ч: перехватов {takeover} · "
            "неатрибутированных пауз {unattributed} · неопознанных исходящих {unknown}"
        ),
        "status_bot_active": "🟢 Бот активен",
        "status_active_footer": "🟢 Бот активен · авто-возврат: прогон {gap} назад",
        "status_stopped_footer": "🔴 Бот остановлен (/stop). Снять: /start",
        "status_autoresume_never": "⚠️ Авто-возврат: НИ РАЗУ не отработал",
        "status_autoresume_stale": "⚠️ Авто-возврат: последний прогон {gap} назад",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "список устарел, набери /status",
        "no_such_number": "нет такого номера, набери /status",
        "card_header": "⏸ Пауза: {name}",
        "card_intervened_detail": "Вы вмешались: «{detail}»",
        "card_silent": "{persona} молчит в этом диалоге.",
        "card_resume_reply_hint": "Ответьте /resume на это сообщение",
        "card_resume_status_hint": "или: /status → /resume <номер>",
        # Арка 3B — кнопки, карточка эскалации, feedback на тап, алерт деградации.
        "btn_resume": "▶️ Вернуть Аню",
        "btn_snooze": "⏸ Ещё 1ч",
        "btn_open": "💬 Открыть диалог",
        "btn_stop": "🔴 Стоп везде",
        "btn_keep": "✅ Оставить Ане",
        "esc_header": "🔴 Горячий лид: {name}",
        "esc_wants": "Хочет: {summary}",
        "esc_why": "Почему: {reason}",
        "esc_recent_header": "Последние сообщения:",
        "esc_role_lead": "Клиент",
        "esc_role_persona": "Ассистент",
        "fb_resumed": "▶️ Аня вернулась в диалог",
        "fb_snoozed": "⏸ Пауза ещё на час",
        "fb_stopped": "🔴 Аня остановлена во всех диалогах",
        "fb_kept": "✅ Оставлено Ане",
        "fb_open": "💬 Открыть диалог: {link}",
        "fb_unknown": "не понял действие",
        "fb_not_owner": "эта кнопка не для вас",
        "bind_welcome": "✅ Пульт подключён. Карточки пауз и эскалаций будут приходить сюда.",
        "bind_welcome_back": "✅ Пульт уже подключён к вам.",
        "bind_rejected": "⛔ Этот бот уже привязан к другому владельцу.",
        "bind_not_authorized": "⛔ Доступ запрещён. Нужен код: /start <код> от продавца.",
        "unbind_ack": "🔓 Пульт отвязан. Следующий /start привяжет заново.",
        "inline_cmd_notice": "✅ Выполнил {cmd} из диалога. Совет: команды набирайте здесь, в пульте — в самом диалоге их видит лид.",
        "known_contact_notice": "👤 Знакомый {name} написал: «{snippet}». Аня знакомым не отвечает — ответь сам. (чтобы вести его как лида, добавь id {id} в allowlist настроек)",
        "degraded_alert": (
            "⚠️ Классификатор деградировал: {count} ошибок за {hours}ч. "
            "Эскалации сейчас идут только по ключевым словам."
        ),
        "help_text": (
            "📖 Пульт Ани — как это работает\n\n"
            "Аня отвечает лидам сама. Как только вы напишете в диалог руками — "
            "Аня ЗАМОЛКАЕТ в этом диалоге (это нарочно: чтобы не отвечать поверх "
            "вас). В Saved Messages появится карточка паузы.\n\n"
            "Вернуть Аню — 3 способа:\n"
            "• Ответьте /resume на карточку паузы (реплаем) — проще всего.\n"
            "• /status покажет список пауз с номерами → /resume <номер>.\n"
            "• /resume <@юзернейм|ссылка|id> — если карточки уже нет под рукой.\n\n"
            "Команды:\n"
            "/status — список всех пауз: имя, причина, готовая команда возврата.\n"
            "/pause [1h|30m] [@user|ссылка|id] — заглушить диалог "
            "(без времени = насовсем).\n"
            "/resume [<номер>|@user|ссылка|id] — вернуть Аню в диалог.\n"
            "/stop — заглушить Аню ВЕЗДЕ, во всех диалогах разом.\n"
            "/start — снять глобальную заглушку.\n"
            "/help — эта справка."
        ),
    },
    "en": {
        "status_header": "⏸ Paused ({n}):",
        "status_intervened": "you stepped in {gap} ago",
        "status_paused_for": "/pause {duration}, {remaining} left",
        "status_paused_indefinite": "/pause indefinitely",
        "status_quote": "“{detail}”",
        "status_window_counters": (
            "Last {window_hours}h: takeovers {takeover} · "
            "unattributed pauses {unattributed} · unknown outgoing {unknown}"
        ),
        "status_bot_active": "🟢 Bot active",
        "status_active_footer": "🟢 Bot active · auto-resume: last run {gap} ago",
        "status_stopped_footer": "🔴 Bot stopped (/stop). Lift with: /start",
        "status_autoresume_never": "⚠️ Auto-resume: has NEVER run",
        "status_autoresume_stale": "⚠️ Auto-resume: last run {gap} ago",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "list is stale, run /status",
        "no_such_number": "no such number, run /status",
        "card_header": "⏸ Paused: {name}",
        "card_intervened_detail": "You stepped in: «{detail}»",
        "card_silent": "{persona} is silent in this chat.",
        "card_resume_reply_hint": "Reply /resume to this message",
        "card_resume_status_hint": "or: /status → /resume <number>",
        "btn_resume": "▶️ Bring Anya back",
        "btn_snooze": "⏸ +1h",
        "btn_open": "💬 Open chat",
        "btn_stop": "🔴 Stop everywhere",
        "btn_keep": "✅ Leave it to Anya",
        "esc_header": "🔴 Hot lead: {name}",
        "esc_wants": "Wants: {summary}",
        "esc_why": "Why: {reason}",
        "esc_recent_header": "Recent messages:",
        "esc_role_lead": "Lead",
        "esc_role_persona": "Assistant",
        "fb_resumed": "▶️ Anya is back in the chat",
        "fb_snoozed": "⏸ Paused for another hour",
        "fb_stopped": "🔴 Anya stopped in all chats",
        "fb_kept": "✅ Left to Anya",
        "fb_open": "💬 Open chat: {link}",
        "fb_unknown": "didn't get that action",
        "fb_not_owner": "this button isn't for you",
        "bind_welcome": "✅ Console connected. Pause and escalation cards will arrive here.",
        "bind_welcome_back": "✅ Console is already connected to you.",
        "bind_rejected": "⛔ This bot is already bound to another owner.",
        "bind_not_authorized": "⛔ Access denied. You need a code: /start <code> from the vendor.",
        "unbind_ack": "🔓 Console unbound. The next /start will bind it again.",
        "inline_cmd_notice": "✅ Ran {cmd} from the chat. Tip: type commands here in the console — the lead sees them in the chat itself.",
        "known_contact_notice": "👤 A known contact {name} wrote: «{snippet}». Anya does not answer contacts — reply yourself. (to treat them as a lead, add id {id} to the settings allowlist)",
        "degraded_alert": (
            "⚠️ Classifier degraded: {count} errors in {hours}h. "
            "Escalations currently fire on keywords only."
        ),
        "help_text": (
            "📖 Anya's console — how this works\n\n"
            "Anya replies to leads on her own. The moment you write into a chat "
            "by hand, Anya goes SILENT in that chat (on purpose: so she never "
            "talks over you). A pause card shows up in Saved Messages.\n\n"
            "3 ways to bring Anya back:\n"
            "• Reply /resume to the pause card — easiest.\n"
            "• /status lists paused chats with numbers → /resume <number>.\n"
            "• /resume <@username|link|id> — if the card scrolled away.\n\n"
            "Commands:\n"
            "/status — every paused chat: name, reason, ready-to-copy resume command.\n"
            "/pause [1h|30m] [@user|link|id] — pause a chat (no time = indefinitely).\n"
            "/resume [<number>|@user|link|id] — bring Anya back to a chat.\n"
            "/stop — silence Anya EVERYWHERE, all chats at once.\n"
            "/start — lift the global silence.\n"
            "/help — this text."
        ),
    },
    "uk": {
        "status_header": "⏸ Паузи ({n}):",
        "status_intervened": "ви втрутилися {gap} тому",
        "status_paused_for": "/pause {duration}, залишилось {remaining}",
        "status_paused_indefinite": "/pause безстроково",
        "status_quote": "«{detail}»",
        "status_window_counters": (
            "За {window_hours}год: перехоплень {takeover} · "
            "неатрибутованих пауз {unattributed} · невпізнаних вихідних {unknown}"
        ),
        "status_bot_active": "🟢 Бот активний",
        "status_active_footer": "🟢 Бот активний · авто-повернення: запуск {gap} тому",
        "status_stopped_footer": "🔴 Бот зупинено (/stop). Зняти: /start",
        "status_autoresume_never": "⚠️ Авто-повернення: ЖОДНОГО разу не спрацювало",
        "status_autoresume_stale": "⚠️ Авто-повернення: останній запуск {gap} тому",
        "resume_hint": "→ /resume {n}",
        "list_is_stale": "список застарів, наберіть /status",
        "no_such_number": "немає такого номера, наберіть /status",
        "card_header": "⏸ Пауза: {name}",
        "card_intervened_detail": "Ви втрутилися: «{detail}»",
        "card_silent": "{persona} мовчить у цьому діалозі.",
        "card_resume_reply_hint": "Відповідайте /resume на це повідомлення",
        "card_resume_status_hint": "або: /status → /resume <номер>",
        "btn_resume": "▶️ Повернути Аню",
        "btn_snooze": "⏸ Ще 1год",
        "btn_open": "💬 Відкрити діалог",
        "btn_stop": "🔴 Стоп скрізь",
        "btn_keep": "✅ Залишити Ані",
        "esc_header": "🔴 Гарячий лід: {name}",
        "esc_wants": "Хоче: {summary}",
        "esc_why": "Чому: {reason}",
        "esc_recent_header": "Останні повідомлення:",
        "esc_role_lead": "Клієнт",
        "esc_role_persona": "Асистент",
        "fb_resumed": "▶️ Аня повернулася в діалог",
        "fb_snoozed": "⏸ Пауза ще на годину",
        "fb_stopped": "🔴 Аню зупинено в усіх діалогах",
        "fb_kept": "✅ Залишено Ані",
        "fb_open": "💬 Відкрити діалог: {link}",
        "fb_unknown": "не зрозумів дію",
        "fb_not_owner": "ця кнопка не для вас",
        "bind_welcome": "✅ Пульт підключено. Картки пауз і ескалацій приходитимуть сюди.",
        "bind_welcome_back": "✅ Пульт уже підключено до вас.",
        "bind_rejected": "⛔ Цей бот уже прив'язаний до іншого власника.",
        "bind_not_authorized": "⛔ Доступ заборонено. Потрібен код: /start <код> від продавця.",
        "unbind_ack": "🔓 Пульт відв'язано. Наступний /start прив'яже знову.",
        "inline_cmd_notice": "✅ Виконав {cmd} з діалогу. Порада: команди набирайте тут, у пульті — у самому діалозі їх бачить лід.",
        "known_contact_notice": "👤 Знайомий {name} написав: «{snippet}». Аня знайомим не відповідає — відповідай сам. (щоб вести його як ліда, додай id {id} у allowlist налаштувань)",
        "degraded_alert": (
            "⚠️ Класифікатор деградував: {count} помилок за {hours}год. "
            "Ескалації зараз лише за ключовими словами."
        ),
        "help_text": (
            "📖 Пульт Ані — як це працює\n\n"
            "Аня відповідає лідам сама. Щойно ви напишете в діалог власноруч — "
            "Аня ЗАМОВКАЄ в цьому діалозі (це навмисно: щоб не відповідати "
            "поверх вас). У Saved Messages з'явиться картка паузи.\n\n"
            "Повернути Аню — 3 способи:\n"
            "• Дайте відповідь /resume на картку паузи (реплаєм) — найпростіше.\n"
            "• /status покаже список пауз із номерами → /resume <номер>.\n"
            "• /resume <@юзернейм|посилання|id> — якщо картки вже нема під рукою.\n\n"
            "Команди:\n"
            "/status — список усіх пауз: ім'я, причина, готова команда повернення.\n"
            "/pause [1h|30m] [@user|посилання|id] — заглушити діалог "
            "(без часу = назавжди).\n"
            "/resume [<номер>|@user|посилання|id] — повернути Аню в діалог.\n"
            "/stop — заглушити Аню СКРІЗЬ, у всіх діалогах одразу.\n"
            "/start — зняти глобальну заглушку.\n"
            "/help — ця довідка."
        ),
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
    Telethon), форматтер остаётся чистым.

    `n` — номер из последнего /status (`status_index`, спека 3A-UX §3).
    ОБЯЗАН совпадать с тем `n`, под которым раннер зарегистрировал этот же
    contact_id в `store.issue_status_index(...)` -- иначе печатаемый номер
    и адресуемый номер разъедутся, а это и есть тот самый промах в чужой
    диалог, от которого вся эта под-арка (см. TelethonRunner.render_status).

    `title` уже HTML-экранирован вызывающей стороной (это результат
    `display_name(...)`) -- формат сам его не эскейпит повторно, только
    защитно схлопывает переводы строк (`_one_line`, старая находка 3A).
    `detail`, напротив, СЫРОЙ (прямая цитата владельца из БД) -- формат
    обязан прогнать его через `safe_snippet` сам."""
    n: int
    title: str
    link: str
    since_ts: float
    source: str
    detail: str | None
    msg_id: int | None
    resume_eta_ts: float | None


def html_link(text_html: str, href: str) -> str:
    """Кликабельное имя (спека §7): `<a href="...">текст</a>`.

    `href` не экранируется: единственные вызывающие -- `contact_link(...)`
    (t.me/<username копия сервера ограничена [A-Za-z0-9_]> или
    tg://user?id=<целое число>) -- туда физически не может попасть символ,
    ломающий атрибут HTML."""
    return f'<a href="{href}">{text_html}</a>'


def _duration_token(seconds: float) -> str:
    """Обратное превращение секунд в токен `_DURATION_RE` (`1h`/`30m`),
    ЧТОБЫ ПОКАЗАТЬ ВЛАДЕЛЬЦУ ТУ ЖЕ КОМАНДУ, КОТОРУЮ ОН НАБРАЛ (спека §2:
    "/pause 1h, осталось 42 мин" -- буквально те же буквы, что принимает
    парсер, не "1ч" человеческим языком, как в `_humanize_gap`). `/pause`
    принимает только целые часы/минуты (`_DURATION_RE`), поэтому секунды,
    полученные как `resume_eta_ts - since_ts`, всегда кратны 3600 или 60 --
    восстановление токена не теряет точность."""
    seconds = max(1, int(round(seconds)))
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(1, seconds // 60)}m"


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
    now: float, window_hours: int, language: str = "ru",
) -> str:
    """Ответ на «почему Аня молчит» за 5 секунд -- и главное, ЧТО НАБРАТЬ,
    чтобы её вернуть: команда `/resume {n}` печатается ПРЯМО под каждой
    паузой (спека 3A-UX §2), владелец копирует её глазами, а не сочиняет.

    Порядок печати пауз в `pauses` НЕ переопределяется здесь (просто
    итерируется как дан) -- инвариант "напечатанный номер == номер в
    status_index" держится за счёт того, что вызывающая сторона
    (`TelethonRunner.render_status`) строит `pauses` и вызывает
    `store.issue_status_index(...)` ИЗ ОДНОГО и ТОГО ЖЕ enumerate(rows),
    см. комментарий там."""
    lines = [console_text("status_header", language, n=len(pauses))]
    for p in pauses:
        title = _one_line(p.title)          # защитный слой, см. PauseView
        name_html = html_link(title, p.link)
        if p.source == "human_takeover":
            reason = console_text(
                "status_intervened", language, gap=_humanize_gap(now - p.since_ts))
        elif p.resume_eta_ts is not None:
            reason = console_text(
                "status_paused_for", language,
                duration=_duration_token(p.resume_eta_ts - p.since_ts),
                remaining=_humanize_gap(p.resume_eta_ts - now))
        else:
            reason = console_text("status_paused_indefinite", language)
        lines.append(f"{p.n}. {name_html} — {reason}")
        if p.detail:
            # safe_snippet, НЕ _one_line: этой строке ещё нужно экранирование
            # (p.detail -- сырая цитата владельца из БД, см. PauseView).
            lines.append(f"   {console_text('status_quote', language, detail=safe_snippet(p.detail))}")
        lines.append(f"   {console_text('resume_hint', language, n=p.n)}")

    if counters:
        lines.append(console_text(
            "status_window_counters", language, window_hours=window_hours,
            takeover=counters.get("takeover", 0),
            unattributed=counters.get("unattributed_pause", 0),
            unknown=counters.get("unknown_outgoing", 0)))

    # Кто сторожит сторожа: мёртвая задача авто-возврата неотличима от
    # «пауз к возврату нет», если не показать возраст её heartbeat. Отдельно
    # ловим beat_age=None (задача НИ РАЗУ не отработала — раннер только что
    # стартовал, или задача умерла ещё до первого прогона): сравнение None с
    # числом упало бы TypeError, а молчаливый пропуск проверки скрыл бы ровно
    # тот случай, который эта строка обязана заметить (DEV-18).
    #
    # Глобальное состояние (kill switch) и здоровье авто-возврата -- ДВЕ
    # независимые оси: раньше они были одной строкой ("Бот активен · авто-
    # возврат: X"), что для здорового случая экономит строку (совпадает с
    # целевым видом спеки §2), но для больного случая соврало бы -- "бот
    # активен" не может стоять в одной фразе с "⚠️ авто-возврат мёртв" так,
    # будто это одна хорошая новость.
    autoresume_healthy = (
        autoresume_beat_age is not None and autoresume_beat_age <= 3 * autoresume_interval)
    if kill_switch:
        lines.append(console_text("status_stopped_footer", language))
        if not autoresume_healthy:
            lines.append(_autoresume_warning(language, autoresume_beat_age))
    elif autoresume_healthy:
        lines.append(console_text("status_active_footer", language, gap=_humanize_gap(autoresume_beat_age)))
    else:
        lines.append(console_text("status_bot_active", language))
        lines.append(_autoresume_warning(language, autoresume_beat_age))
    return "\n".join(lines)


def _autoresume_warning(language: str, beat_age: float | None) -> str:
    if beat_age is None:
        return console_text("status_autoresume_never", language)
    return console_text("status_autoresume_stale", language, gap=_humanize_gap(beat_age))


# ---------------------------------------------------------------------------
# Арка 3B: наборы кнопок + карточка эскалации. Button/Action живут в
# chatter.notify.base (односторонняя зависимость console -> notify.base, без
# цикла: notify.base ничего из console не импортирует).
# ---------------------------------------------------------------------------
from chatter.notify.base import Action, Button  # noqa: E402


_BUTTON_LABEL_KEY = {
    Action.RESUME: "btn_resume",
    Action.SNOOZE: "btn_snooze",
    Action.OPEN: "btn_open",
    Action.STOP: "btn_stop",
    Action.KEEP: "btn_keep",
}


def _buttons(actions: list[Action], language: str) -> list[Button]:
    return [Button(action=a, label=console_text(_BUTTON_LABEL_KEY[a], language)) for a in actions]


def escalation_buttons(language: str = "ru") -> list[Button]:
    """Полный набор карточки эскалации (§2/§3): вернуть, ещё 1ч, открыть, стоп,
    оставить Ане."""
    return _buttons(
        [Action.RESUME, Action.SNOOZE, Action.OPEN, Action.STOP, Action.KEEP], language)


def pause_buttons(language: str = "ru") -> list[Button]:
    """Карточка паузы: как эскалация, но без «Оставить Ане» (это не эскалация,
    диалог уже на паузе)."""
    return _buttons([Action.RESUME, Action.SNOOZE, Action.OPEN, Action.STOP], language)


def format_escalation_card(
    *, name_html: str, link: str, summary: str, reason: str,
    recent: list[tuple[str, str]], language: str = "ru",
    persona_name: str | None = None, recent_limit: int = 5,
) -> str:
    """Карточка эскалации, по которой владелец решает за 3 секунды (§3):
    кто (кликабельное имя) · что хочет (summary одной строкой) · последние
    3-5 реплик · почему эскалировано.

    `name_html` уже HTML-экранирован (результат display_name/html_link).
    `summary`/`reason` — доверенные короткие строки (наши или reason
    классификатора), но экранируем защитно. Текст реплик (`recent`) — СЫРОЙ
    (слова лида/персоны), гоним через safe_snippet."""
    lead = console_text("esc_role_lead", language)
    persona = persona_name or console_text("esc_role_persona", language)
    lines = [
        console_text("esc_header", language, name=name_html),
        console_text("esc_wants", language, summary=safe_snippet(summary, limit=200)),
        console_text("esc_why", language, reason=safe_snippet(reason, limit=120)),
        "",
        console_text("esc_recent_header", language),
    ]
    for role, text in recent[-recent_limit:]:
        who = escape_html(lead if role == "user" else persona)
        lines.append(f"<b>{who}:</b> {safe_snippet(text, limit=200)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Config-арка: /config /reload /knowledge /rollback — человекочитаемо, i18n.
# ---------------------------------------------------------------------------
_CFG_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "cfg_header": "⚙️ Настройки",
        "cfg_persona": "Персона: {name}",
        "cfg_persona_aged": "Персона: {name}, {age} лет",
        "cfg_language": "Язык: {language}",
        "cfg_model": "Модель: {model}",
        "cfg_knowledge": "База знаний: {sections} разделов, {bullets} пунктов",
        "cfg_gate_on": "Гейт воронки: ВКЛ (незнакомцы→лиды, знакомые→уведомление)",
        "cfg_gate_off": "Гейт воронки: выкл (отвечаю только allowlist)",
        "cfg_lists": "Списки: allowlist {allow}, denylist {deny}",
        "cfg_changed": "Конфиг менялся: {ago}",
        "cfg_changed_never": "Конфиг менялся: с запуска не менялся",
        "cfg_reload_ok": "✅ Конфиг перечитан. Аня отвечает по новому.",
        "cfg_reload_fail": "⚠️ Не перечитал ({reason}). Работаю на ПРЕЖНЕМ конфиге — Аня не замолчала.",
        "cfg_kb_current": "📚 База знаний:\n\n{knowledge}",
        "cfg_kb_updated": "✅ База знаний обновлена ({n} симв.) и перечитана.",
        "cfg_kb_empty": "⚠️ Пустой текст — база знаний не может быть пустой.",
        "cfg_rollback_ok": "↩️ Откатил конфиг на предыдущую версию и перечитал.",
        "cfg_rollback_none": "Нет предыдущей версии для отката.",
        "cfg_rollback_fail": "⚠️ Откат не удался ({reason}). Остаюсь на текущем.",
        "cfg_unknown": "неизвестная config-команда",
    },
    "en": {
        "cfg_header": "⚙️ Settings",
        "cfg_persona": "Persona: {name}",
        "cfg_persona_aged": "Persona: {name}, age {age}",
        "cfg_language": "Language: {language}",
        "cfg_model": "Model: {model}",
        "cfg_knowledge": "Knowledge: {sections} sections, {bullets} items",
        "cfg_gate_on": "Funnel gate: ON (strangers→leads, contacts→notice)",
        "cfg_gate_off": "Funnel gate: off (answering allowlist only)",
        "cfg_lists": "Lists: allowlist {allow}, denylist {deny}",
        "cfg_changed": "Config changed: {ago}",
        "cfg_changed_never": "Config changed: unchanged since start",
        "cfg_reload_ok": "✅ Config reloaded. Anya now uses the new one.",
        "cfg_reload_fail": "⚠️ Reload failed ({reason}). Running on the PREVIOUS config — Anya kept serving.",
        "cfg_kb_current": "📚 Knowledge base:\n\n{knowledge}",
        "cfg_kb_updated": "✅ Knowledge updated ({n} chars) and reloaded.",
        "cfg_kb_empty": "⚠️ Empty text — the knowledge base can't be empty.",
        "cfg_rollback_ok": "↩️ Rolled config back to the previous version and reloaded.",
        "cfg_rollback_none": "No previous version to roll back to.",
        "cfg_rollback_fail": "⚠️ Rollback failed ({reason}). Staying on current.",
        "cfg_unknown": "unknown config command",
    },
    "uk": {
        "cfg_header": "⚙️ Налаштування",
        "cfg_persona": "Персона: {name}",
        "cfg_persona_aged": "Персона: {name}, {age} р.",
        "cfg_language": "Мова: {language}",
        "cfg_model": "Модель: {model}",
        "cfg_knowledge": "База знань: {sections} розділів, {bullets} пунктів",
        "cfg_gate_on": "Гейт воронки: УВІМК (незнайомці→ліди, знайомі→сповіщення)",
        "cfg_gate_off": "Гейт воронки: вимк (відповідаю лише allowlist)",
        "cfg_lists": "Списки: allowlist {allow}, denylist {deny}",
        "cfg_changed": "Конфіг змінювався: {ago}",
        "cfg_changed_never": "Конфіг змінювався: з запуску не змінювався",
        "cfg_reload_ok": "✅ Конфіг перечитано. Аня відповідає по-новому.",
        "cfg_reload_fail": "⚠️ Не перечитав ({reason}). Працюю на ПОПЕРЕДНЬОМУ конфігу — Аня не замовкла.",
        "cfg_kb_current": "📚 База знань:\n\n{knowledge}",
        "cfg_kb_updated": "✅ Базу знань оновлено ({n} симв.) і перечитано.",
        "cfg_kb_empty": "⚠️ Порожній текст — база знань не може бути порожньою.",
        "cfg_rollback_ok": "↩️ Відкотив конфіг на попередню версію і перечитав.",
        "cfg_rollback_none": "Немає попередньої версії для відкату.",
        "cfg_rollback_fail": "⚠️ Відкат не вдався ({reason}). Залишаюся на поточному.",
        "cfg_unknown": "невідома config-команда",
    },
}


def cfg_text(key: str, lang: str = "ru", **kwargs) -> str:
    # Параметр называется `lang` (а не `language`), потому что среди kwargs
    # шаблонов есть плейсхолдер {language} (язык персоны) — одноимённый
    # позиционный параметр столкнулся бы с ним.
    strings = _CFG_STRINGS.get(lang, _CFG_STRINGS["ru"])
    t = strings[key]
    return t.format(**kwargs) if kwargs else t


def knowledge_stats(knowledge: str) -> tuple[int, int]:
    """(разделов ##, пунктов -). Грубая метрика «сколько позиций» для /config."""
    sections = bullets = 0
    for line in (knowledge or "").splitlines():
        s = line.strip()
        if s.startswith("## "):
            sections += 1
        elif s.startswith("- "):
            bullets += 1
    return sections, bullets


def format_config(
    *, persona_name: str, persona_age: int | None, language: str, model: str,
    knowledge: str, funnel_gate: bool, allow_count: int, deny_count: int,
    changed_ago: str | None, lang: str = "ru",
) -> str:
    sections, bullets = knowledge_stats(knowledge)
    persona_line = (
        cfg_text("cfg_persona_aged", lang, name=escape_html(persona_name), age=persona_age)
        if persona_age is not None
        else cfg_text("cfg_persona", lang, name=escape_html(persona_name)))
    lines = [
        cfg_text("cfg_header", lang),
        persona_line,
        cfg_text("cfg_language", lang, language=language),
        cfg_text("cfg_model", lang, model=escape_html(model)),
        cfg_text("cfg_knowledge", lang, sections=sections, bullets=bullets),
        cfg_text("cfg_gate_on" if funnel_gate else "cfg_gate_off", lang),
        cfg_text("cfg_lists", lang, allow=allow_count, deny=deny_count),
        cfg_text("cfg_changed", lang, ago=changed_ago) if changed_ago
        else cfg_text("cfg_changed_never", lang),
    ]
    return "\n".join(lines)
