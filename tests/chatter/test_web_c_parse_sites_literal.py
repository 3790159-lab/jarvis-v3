# -*- coding: utf-8 -*-
"""Сторожа 13, 14 и 16 спеки «ВЕБ, волна 2 / пара C» — ЛИТЕРАЛЬНЫЕ ПЕРЕПИСИ.

Спека: `docs/superpowers/specs/2026-08-28-web-c-envelope-identity.md`,
§1.2, §3.2, §8 п.3, §9 (сторожа 13, 14, 16).

ПОЧЕМУ ЭТОТ ФАЙЛ ВООБЩЕ СУЩЕСТВУЕТ. Перепись мест разбора уже ДВАЖДЫ занизила
состав по одному шаблону: волна 1 нашла восемь по образцу `contact_id.split(":"`,
замер 28.08 повторил перепись ПО ФОРМАМ и нашёл ещё четыре — все четыре режут
не переменную с именем `contact_id`, а строку, которая `contact_id` ЯВЛЯЕТСЯ
(§8 п.3). Отсюда правило спеки: **перечислять ФОРМЫ, а не имена**, и держать
список ЛИТЕРАЛЬНЫМ ([[jarvis-literal-lists-not-introspection]]): список,
выведенный интроспекцией, согласен с кодом по определению и молчит ровно там,
где код забыл.

ЗДЕСЬ НЕТ НИ ОДНОГО ИМПОРТА КОДА АРКИ — намеренно. Файл обязан работать и ДО
неё, и ПОСЛЕ: утверждение «ручного разбора больше не осталось» — это
утверждение о ДЕРЕВЕ, и никакой прогон его не докажет (девятое место, которое
просто не позвали, не покраснеет никогда). Это оговорённое спекой исключение
из домашнего правила «мерим ИСХОД, а не текст».

Корень репозитория — `parents[2]` от файла: сторожа обязаны работать в ЛЮБОМ
worktree, а не только в боевом дереве (DEV-78).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("chatter", "app", "scripts")

# ═══════════════════════════════════════════════════════════════════════════
# §9 сторож 13: ДВЕНАДЦАТЬ мест разбора структуры `contact_id`, ЛИТЕРАЛЬНО
#
# Ключ — `<путь>::<функция>`, а НЕ `<путь>:<строка>`. Номера строк уже уезжали
# между замерами 24.08 и 28.08 (спека сама это отмечает про `:1397` → `:1398`),
# и сторож на номерах краснел бы от каждой правки соседнего абзаца — то есть
# перестал бы читаться.
#
# `closed` — какой волной место закрывается: 1 (пара A, уже сделано) или
# 2 (эта арка). `form` — та форма разбора, которой место ловится переписью.
# ═══════════════════════════════════════════════════════════════════════════

PARSE_SITES_12: dict[str, dict] = {
    # ── восемь мест волны 1 (пара A, §1 её спеки) ────────────────────────
    "chatter/telethon_run.py::build_escalation_card": dict(
        closed=1, form='int(contact_id.split(":", 1)[0])',
        why="АДРЕСАЦИЯ карточки эскалации: голова шла в telegram-peer. "
            "На новой форме `int('telegram')` бросил бы ГРОМКО."),
    "chatter/telethon_run.py::render_status": dict(
        closed=1, form='int(row["contact_id"].split(":")[0])',
        why="/status владельцу. Вторая ФОРМА написания того же разбора — "
            "правка, причесавшая только `contact_id.split`, оставила бы её."),
    "chatter/run.py::_post_invoice_card": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ПОДПИСЬ в карточке счёта. На новой форме вернула бы 'telegram' "
            "МОЛЧА — и это доехало бы до владельца как имя лида."),
    "chatter/run.py::_post_escalation_card": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ПОДПИСЬ в карточке эскалации, тот же молчаливый исход."),
    "chatter/run.py::_note_profile_miss": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ПОДПИСЬ в алерте о серии промахов профиля."),
    "chatter/run.py::_maybe_stale_card_notice": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ПОДПИСЬ в извещении о протухшей карточке."),
    "chatter/notify/control_bot.py::_peer_of": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ссылка на диалог в карточке пульта (`contact_link`)."),
    "app/services/tamapi_metrics.py::_peer": dict(
        closed=1, form='contact_id.split(":", 1)[0]',
        why="ПОДПИСЬ контакта в ленте панели, когда `display_name` пуст."),

    # ── четыре места волны 2: их закрывает ЭТА арка (§1.2) ───────────────
    "chatter/telethon_run.py::deliver_outgoing": dict(
        closed=2, form='contact_id.rpartition(":")  ->  int(peer_part)',
        why="🔴 САМОЕ ДОРОГОЕ И ЕДИНСТВЕННОЕ ГРОМКОЕ. Доставка задания из "
            "`outgoing_queue` — единственный путь «человек написал лиду из "
            "панели». Разбор fail-closed: `int()` бросает, отказ становится "
            "`Refusal('unknown_contact')`. Отказ ВИДЕН (last_error, лампа "
            "возраста), но задание контакту нового формата не доставится "
            "НИКОГДА, а продукт при этом стоит."),
    "chatter/connect/actions.py::_drill_ids": dict(
        closed=2, form='str(entry).partition(":")  ->  persona == slug',
        why="🔇 `_drill_ids` вернёт ПУСТО. Переменная зовётся `entry`, а не "
            "`contact_id` — ровно поэтому ценз волны 1 её не увидел."),
    "chatter/connect/probes.py::_drill_contact_ids": dict(
        closed=2, form='contact.partition(":")  ->  tail != slug',
        why="🔇 проба S9 скажет «дрил-контакта нет», хотя он есть. "
            "Переменная зовётся `contact`."),
    "chatter/storage/db.py::_slug": dict(
        closed=2, form='(contact_id or "").partition(":")  ->  хвост',
        why="🔇 хвост уедет в PRIMARY KEY счёта: `Q-8849893367:volska-000001` "
            "вместо `Q-volska-000001`. Молча и навсегда — id счёта не "
            "переписывают."),
}

# Имена, через которые место разбора СЧИТАЕТСЯ закрытым: единственная точка
# правды об устройстве `contact_id` (пара A) плюс конверт (эта арка).
OWNER_MODULES = ("chatter.core.contact_ref", "chatter.core.inbound")

# ═══════════════════════════════════════════════════════════════════════════
# ФОРМЫ РАЗБОРА. Перечисляем формы, а не имена переменных (§8 п.3) — именно
# по этой причине список ниже ловит `entry`, `contact`, `row["contact_id"]` и
# `handle.ref` одинаково.
# ═══════════════════════════════════════════════════════════════════════════

STRING_FORMS = ("split", "rsplit", "partition", "rpartition",
                "startswith", "endswith", "index", "find",
                "removeprefix", "removesuffix")

# Разделитель `contact_id` — ГОЛОЕ двоеточие; головы каналов из литерального
# реестра §3.1 добавлены, чтобы `startswith("telegram:")` тоже ловился.
CONTACT_SEPARATORS = (":", "telegram:", "web:")

# Имена, которые ОБОЗНАЧАЮТ contact_id, — для форм, у которых разделителя нет
# (срез, регулярка).
CONTACT_NAMES = ("contact_id", "cid", "contact", "entry")

# ═══════════════════════════════════════════════════════════════════════════
# ВСТРЕЧНАЯ ПОЛОВИНА сторожа 13: всё остальное, что режет по двоеточию и
# `contact_id` НЕ разбирает. Список ЛИТЕРАЛЬНЫЙ и с причиной на пункт — без
# причины через месяц он читается как «вот эти почему-то можно», и следующий
# дописывающий не знает, чему обязан соответствовать.
#
# Всякое исключение — дыра, поэтому у списка есть СВОЯ встречная половина
# (`test_guard13_spisok_isklyucheniy_ne_protuh`): исключение на функцию,
# которой в дереве больше нет, ничего не освобождает, зато прячет от сторожа
# путь, по которому завтра ляжет новый разбор.
# ═══════════════════════════════════════════════════════════════════════════

COLON_PARSE_EXEMPT: dict[str, str] = {
    # Ключ — `<путь>::<функция> || <разбираемое выражение>`. С ВЫРАЖЕНИЕМ, а не
    # просто с функцией: `_post_invoice_card` и `_post_escalation_card` делают
    # ОБЕ вещи сразу — зовут владельца разбора для contact_id И режут по
    # двоеточию `handle.ref`. Исключение на всю функцию накрыло бы заодно и
    # возврат ручного разбора contact_id в неё.

    # --- `handle.ref` пульта: "bot:<chat_id>:<msg_id>". Замер §1.1 проверил
    #     это отдельно: в runtime_flags.value лежит именно handle.ref, а не
    #     contact_id; все места берут ХВОСТ и удлинение переживают.
    "chatter/run.py::_post_invoice_card || handle.ref":
        "handle.ref `bot:<chat>:<msg>` → [-1], берёт ХВОСТ",
    "chatter/run.py::_post_escalation_card || handle.ref":
        "handle.ref пульта, берёт хвост",
    "chatter/run.py::_card_msg_id || raw":
        "значение флага esc_active — это handle.ref; rsplit(':',1)[1], хвост",
    "chatter/notify/control_bot.py::_edit || handle.ref":
        "handle.ref пульта, берёт хвост",
    "chatter/notify/saved_messages.py::edit || handle.ref":
        "handle.ref пульта, берёт хвост",
    "chatter/notify/saved_messages.py::update_card || handle.ref":
        "handle.ref пульта, берёт хвост",
    "chatter/core/keepalive.py::_alert_broken_prefix || handle.ref":
        "handle.ref пульта, берёт хвост",
    "chatter/telethon_run.py::post_pause_card || handle.ref":
        "handle.ref пульта, берёт хвост",

    # --- разбор `callback_data`, где contact_id — ВЕСЬ хвост (§1.2 «что не
    #     ломается»): удлинение головы переживают ПО РАЗБОРУ. Их отдельный
    #     риск — не форма, а ДЛИНА (§1.3) и старые кнопки (§3.4), и его судят
    #     сторожа 17-20, а не этот.
    "chatter/payments/callbacks.py::parse_callback || data or ''":
        "префикс версии формата; contact_id — весь хвост",
    "chatter/payments/callbacks.py::_parse_paidamt_v1 || rest":
        "`paidamt:<major>:<contact_id>`; contact_id — весь хвост",
    "chatter/payments/callbacks.py::_parse_paidamt_v2 || rest":
        "`paidamt2:<minor>:<ccy>:<contact_id>`; contact_id — весь хвост",
    "chatter/payments/callbacks.py::_parse_paidamt_v2 || tail":
        "второй шаг того же разбора; contact_id — весь хвост",
    "chatter/notify/control_bot.py::route_callback || data or ''":
        "`<action>:<contact_id>`, режет по ПЕРВОМУ двоеточию; contact_id — "
        "весь хвост. Здесь же живёт шов §3.4 (сторож 19)",

    # --- ключ `runtime_flags`: `esc_active:<contact_id>`, берётся ВЕСЬ хвост
    #     (§1.2). Сторож 16 пинит, что их не «причесали» заодно.
    "app/services/tamapi_metrics.py::needs_attention || r['key']":
        "`esc_active:<contact_id>`.split(':',1)[1] — весь хвост",
    "app/services/tamapi_metrics.py::dialog_feed || r['key']":
        "`esc_active:<contact_id>`.split(':',1)[1] — весь хвост",

    # --- СЛУГ-хвост: rsplit по ПОСЛЕДНЕМУ двоеточию, удлинение головы
    #     переживает. Сторож 16 пинит его сохранность.
    "chatter/telethon_run.py::_persona_settings || contact_id":
        "слуг персоны хвостом; при удлинении головы работает как работал",

    # --- к contact_id отношения не имеет вовсе ---------------------------
    "chatter/payments/model.py::validate_dedup_key || key":
        "`dedup_key` = `<source>:<text>`, своя форма",
    "chatter/onboard/render.py::_split_label || head":
        "метка списка в брифе («Заголовок: значение»)",
    "chatter/onboard/render.py::_bullet_block || item.rstrip()":
        "метка списка в брифе",
    "chatter/onboard/render.py::_parse_services || body":
        "метка списка в брифе",
    "chatter/onboard/render.py::_pairs_from_dialog_field || line":
        "пара «ключ: значение» из поля диалога брифа",
    "app/planner.py::_extract_after_colon || original":
        "разбор ПОЛЬЗОВАТЕЛЬСКОЙ фразы, не идентификатора",
    "app/planner.py::_extract_search_query || text":
        "разбор пользовательской фразы",
    "app/services/backup_sandbox.py::repo_roots || text":
        "`gitdir: <путь>` из файла .git",
    "app/services/decision_log.py::parse_feedback_callback || data":
        "своя callback-форма журнала решений",
    "app/services/smart_schedule.py::auto_schedule_tasks || brief_time":
        "время `ЧЧ:ММ`",
    "scripts/monitor_swap_until_done.py::main || prog.split('node')[1]":
        "разбор строки процесса node",
    "scripts/check_proc_env.py::read_env || entry":
        "срез переменной окружения. Ловится потому, что переменная зовётся "
        "`entry` — тем же именем, под которым contact_id живёт в `_drill_ids`. "
        "Широкое имя в переписи — плата за то, чтобы `_drill_ids` не "
        "пропустить: ценз по одному имени уже дважды занизил состав (§8 п.3)",

    # --- миграция §4.2 сама смотрит на форму contact_id через SQL LIKE.
    #     Это НЕ тринадцатое место: это тот единственный код, который обязан
    #     знать, мигрирована база или нет («идемпотентность по ФАКТУ»).
    "chatter/storage/db.py::__init__ || SQL":
        "§4.2: `contact_id NOT LIKE '%:%:%'` — идемпотентность миграции по "
        "ФАКТУ, а не по файлу-маркеру",
    "chatter/storage/db.py::<module> || SQL":
        "§4.2: SQL миграции может лежать константой на уровне модуля",
}


# ═══════════════════════════════════════════════════════════════════════════
# §3.2 / §9 сторож 14: места СБОРКИ `contact_id` f-строкой
#
# ⚠️ СПЕКА НАЗЫВАЕТ ВОСЕМЬ, А В ДЕРЕВЕ ИХ ДЕВЯТЬ. Девятое —
# `scripts/panels_demo.py::seed`, ВТОРАЯ f-строка той же функции
# (`add_event(contact_id=f"{500001}:volska", ...)`, сегодня строка 126).
# Ценз §3.2 посчитал в `panels_demo.py` только сеятель контактов и пропустил
# сеятель событий. Это ТРЕТЬЕ занижение переписи подряд по тому же механизму
# (§8 п.3), поэтому список ниже держит девять, а не восемь: сторож, писанный
# по числу из спеки, зеленел бы на восьми исправленных и девятой оставшейся.
# ═══════════════════════════════════════════════════════════════════════════

ASSEMBLY_SITES_9: dict[str, str] = {
    "chatter/telethon_run.py::contact_id_for_chat":
        "две f-строки в одной функции (:1267 и :1270 на 9b5ff3f7)",
    "chatter/telethon_run.py::resolve_target":
        "адресат команды владельца",
    "chatter/telethon_run.py::handle_event":
        "живое входящее",
    "chatter/telethon_run.py::_on_ready":
        "подъём сессии",
    "chatter/telethon_run.py::_silence_by_decision":
        "заглушение по решению",
    "chatter/telethon_run.py::_process_missed":
        "catch-up пропущенного",
    "scripts/panels_demo.py::seed":
        "стенд панели: сеятель, продолжающий чеканить СТАРУЮ форму, — это "
        "база, которая заново становится немигрированной после каждого "
        "--seed (§3.2). ДВЕ f-строки: контакты и события (девятое место)",
}

# Кому собирать МОЖНО — литерально, с причиной (§3.2: «после арки строку не
# собирает никто, кроме `Inbound.contact_id` и `contact_ref`»).
ASSEMBLY_OWNERS: dict[str, str] = {
    "chatter/core/inbound.py":
        "`Inbound.contact_id` — производное свойство конверта; собирать его "
        "где-то обязан сам конверт, иначе собирать будет снова каждый "
        "вызывающий, то есть ровно тот дефект, ради которого арка затевалась",
    "chatter/core/contact_ref.py":
        "единственная точка правды об устройстве contact_id: обратный ход "
        "разбора (§3.3) живёт там же, где прямой",
}

# ВСТРЕЧНАЯ ПОЛОВИНА сторожа 14: f-строки формы `{a}:{b}`, которые собирают
# НЕ contact_id. Литерально, с причиной на пункт.
FSTRING_EXEMPT: dict[str, str] = {
    "chatter/run.py::_try_redact": "`<правило>:<номер>` в отчёте редакции",
    "chatter/connect/actions.py::_entry_lines": "строка YAML `slug:`",
    "chatter/core/prompt_log.py::obligations_digest": "`<ключ>:<значение>` в дайджесте",
    "chatter/notify/control_bot.py::_keyboard":
        "`<action>:<contact_id>` — это callback_data КНОПКИ, а не contact_id; "
        "contact_id кладётся в неё ЦЕЛИКОМ",
    "chatter/onboard/render.py::_assert_format_hygiene": "`<имя>:<индекс>` в диагностике",
    "chatter/onboard/report.py::_fmt_anchor": "`<файл>:<якорь>` в отчёте",
    "chatter/onboard/report.py::render_markdown": "`<место>:<строка>` в отчёте",
    "chatter/payments/callbacks.py::build_invoice_action":
        "`<kind>:<invoice_id>` — действие адресует СЧЁТ, а не контакт (§14 п.10)",
    "chatter/payments/model.py::make_dedup_key": "`<source>:<text>` — ключ дедупа",
    "chatter/payments/pricing.py::_load_tiered_position": "`<позиция>:<ярус>`",
    "chatter/payments/scope.py::assert_pricing_usable": "`<позиция>:<scope_key>`",
    "chatter/payments/settings.py::assert_startable": "`<позиция>:<scope_key>`",
    "chatter/payments/tier_texts.py::assert_tier_texts_usable": "`<позиция>:<ключ текста>`",
    "chatter/security/crypto.py::restrict_to_system_admins": "`<SID>:<право>` для icacls",
    "app/services/auto_content.py::get_optimal_post_time": "время `ЧЧ:ММ`",
    "app/services/auto_content.py::generate_tomorrow_content": "время `ЧЧ:ММ`",
    "app/services/ig_caption.py::rotate_hashtags": "`<клиент>:<дата>` — ключ ротации",
    "app/services/result_synthesizer.py::synthesize_results": "`<метка>:` в тексте",
    "app/services/smart_schedule.py::get_optimal_brief_time": "время `ЧЧ:ММ`",
    "scripts/backup_keygen.py::restrict_permissions": "`<принципал>:F` для icacls",
    "scripts/jarvis_task_completion_watcher.py::main": "`<task_id>:<путь>` — ключ задачи",
}

_IDENT_ONLY = re.compile(r"^[:A-Za-z0-9_.\-]*$")


# ═══ оснастка обхода ════════════════════════════════════════════════════════

def _python_files() -> list[Path]:
    """Все `.py` под `chatter/`, `app/`, `scripts/`.

    Fail-closed на пропавший корень: сканер, которому нечего сканировать,
    зелен по построению — а это ровно тот способ врать, ради которого сторож
    и написан."""
    found: list[Path] = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        assert base.is_dir(), (
            "каталог %s не найден — сканер AST не увидел бы НИЧЕГО и позеленел "
            "бы впустую; проверь корень репозитория (сейчас %s)." % (base, REPO_ROOT))
        found.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    assert found, "под %r в %s нет ни одного .py — пустой обход даёт зелёный " \
                  "сторож при полностью не проверенном дереве." % (SCAN_ROOTS, REPO_ROOT)
    return found


def _parse(path: Path) -> ast.AST:
    """Разбор с ГРОМКИМ отказом: файл, который сканер не смог прочитать, — это
    файл, в котором он слеп, и его молчание нельзя считать подтверждением
    чистоты.

    `utf-8-sig`, а не `utf-8`: под `app/` лежат сотни файлов с BOM, и наивный
    `read_text('utf-8')` дал бы SyntaxError на каждом — стопка ложных отказов,
    в которой настоящая находка утонет."""
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise AssertionError(
            "%s не разобрался AST (%s); сканер по этому файлу СЛЕП."
            % (path.relative_to(REPO_ROOT).as_posix(), exc)) from exc


def _enclosing(tree: ast.AST) -> list[tuple[int, int, str]]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((n.lineno, n.end_lineno or n.lineno, n.name))
    return out


def _site(rel: str, funcs, lineno: int) -> str:
    best = None
    for start, end, name in funcs:
        if start <= lineno <= end and (best is None or start > best[0]):
            best = (start, name)
    return "%s::%s" % (rel, best[1] if best else "<module>")


def _denotes_contact(node: ast.AST) -> bool:
    """Тот ли это объект: `contact_id`, `row["contact_id"]`, `obj.contact_id`,
    либо одно из имён, под которыми contact_id живёт в дереве (`entry`,
    `contact`) — именно они и увели перепись волны 1 мимо четырёх мест."""
    if isinstance(node, ast.Name):
        return node.id in CONTACT_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in CONTACT_NAMES
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in CONTACT_NAMES
    if isinstance(node, ast.Call):                    # str(entry).partition(...)
        return any(_denotes_contact(a) for a in node.args)
    return False


def _colon_parse_hits() -> dict[str, list[str]]:
    """Все места, разбирающие строку ПО ДВОЕТОЧИЮ — любой из форм §9 п.13.

    Ищем по ФОРМЕ, а не по имени переменной: строковые методы с разделителем
    из `CONTACT_SEPARATORS`, СРЕЗЫ contact_id-подобных имён, РЕГУЛЯРКИ,
    применённые к contact_id-подобному, и SQL-константы с `contact_id ... LIKE`.
    """
    hits: dict[str, list[str]] = {}

    def add(rel, funcs, node, receiver: str, what: str):
        key = "%s || %s" % (_site(rel, funcs, node.lineno), receiver)
        hits.setdefault(key, []).append("%s L%d" % (what, node.lineno))

    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = _parse(path)
        funcs = _enclosing(tree)
        for n in ast.walk(tree):
            # (1) строковые формы с разделителем-двоеточием
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in STRING_FORMS):
                for a in n.args:
                    if (isinstance(a, ast.Constant) and isinstance(a.value, str)
                            and a.value in CONTACT_SEPARATORS):
                        add(rel, funcs, n, ast.unparse(n.func.value),
                            ".%s(%r)" % (n.func.attr, a.value))
                        break
            # (2) СРЕЗ contact_id-подобного имени
            if (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
                    and _denotes_contact(n.value)):
                add(rel, funcs, n, ast.unparse(n.value), "срез")
            # (3) регулярка, применённая к contact_id-подобному
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("match", "search", "fullmatch", "sub", "findall"):
                    subj = None
                    if isinstance(n.func.value, ast.Name) and n.func.value.id == "re":
                        subj = n.args[1] if len(n.args) > 1 else None
                    elif n.args:
                        subj = n.args[0]
                    if subj is not None and _denotes_contact(subj):
                        add(rel, funcs, n, ast.unparse(subj),
                            "регулярка .%s" % n.func.attr)
            # (4) SQL LIKE по contact_id
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if "contact_id" in n.value and re.search(r"\bLIKE\b", n.value, re.I):
                    add(rel, funcs, n, "SQL", "SQL LIKE")
    return hits


def _fstring_assembly_hits() -> dict[str, list[str]]:
    """f-строки формы `{a}:{b}` / `{a}:литерал` — форма СБОРКИ `contact_id`.

    Признак: строка НАЧИНАЕТСЯ с подстановки, литеральная часть содержит ровно
    одно двоеточие и состоит только из символов идентификатора. Так `f"{peer}:
    {slug}"` и `f"{500000 + i}:volska"` ловятся оба, а `f"esc_active:{cid}"`
    (ключ флага, начинается с текста) — нет: ключ собирать никто не запрещал."""
    hits: dict[str, list[str]] = {}
    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = _parse(path)
        funcs = _enclosing(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.JoinedStr) or not n.values:
                continue
            if not isinstance(n.values[0], ast.FormattedValue):
                continue
            lit = "".join(v.value for v in n.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if lit.count(":") != 1 or not _IDENT_ONLY.match(lit):
                continue
            hits.setdefault(_site(rel, funcs, n.lineno), []).append(
                "%s L%d" % (ast.unparse(n)[:60], n.lineno))
    return hits


def _defines(rel: str) -> set[str]:
    tree = _parse(REPO_ROOT / rel)
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _imports_owner(rel: str) -> bool:
    tree = _parse(REPO_ROOT / rel)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            if any(n.module.startswith(m) for m in OWNER_MODULES):
                return True
        if isinstance(n, ast.Import):
            if any(a.name.startswith(m) for m in OWNER_MODULES for a in n.names):
                return True
    return False


# ═══ Сторож 13 ══════════════════════════════════════════════════════════════

def test_guard13_perepis_derzhit_ROVNO_DVENADTSAT_mest():
    """Сторож 13, пин на САМО ЧИСЛО: мест разбора двенадцать (§1.2).

    Число — часть утверждения, а не украшение: волна 1 назвала восемь, замер
    28.08 по формам нашёл ещё четыре. Список, из которого завтра тихо уберут
    строку, чтобы «перестало краснеть», обязан краснеть здесь."""
    assert len(PARSE_SITES_12) == 12, (
        "в переписи %d мест, спека §1.2 утверждает 12 (8 закрыты волной 1, "
        "4 закрываются этой аркой). Если мест стало больше — перепись нашла "
        "тринадцатое и спека обязана быть поправлена ВСЛУХ, а не молча."
        % len(PARSE_SITES_12))
    assert sum(1 for v in PARSE_SITES_12.values() if v["closed"] == 1) == 8
    assert sum(1 for v in PARSE_SITES_12.values() if v["closed"] == 2) == 4


@pytest.mark.parametrize("site", sorted(PARSE_SITES_12))
def test_guard13a_kazhdoe_iz_12_mest_zhivo_i_hodit_cherez_vladeltsa(site):
    """Сторож 13, ПРЯМАЯ половина: каждое из двенадцати мест (а) на месте и
    (б) больше НЕ режет `contact_id` само.

    Параметром на место, а не одним списком: «место исчезло из дерева» и
    «место всё ещё режет строку» — разные диагнозы и разный ремонт, а красное,
    которое не называет, что именно чинить, перестают читать."""
    rel, func = site.split("::")
    path = REPO_ROOT / rel
    assert path.is_file(), (
        "%s значится местом разбора (%s), но файла в дереве НЕТ. Строка "
        "переписи повисла: она ничего не сторожит, зато создаёт вид, что "
        "место проверено." % (rel, PARSE_SITES_12[site]["why"]))
    assert func in _defines(rel), (
        "в %s нет `%s` — место переписи переименовано или исчезло. §9 п.13 "
        "требует краснеть и на это: перепись, потерявшая адрес, дальше "
        "сторожит пустоту." % (rel, func))

    raw = [k for k, v in _colon_parse_hits().items()
           if k.split(" || ")[0] == site and k not in COLON_PARSE_EXEMPT]
    assert not raw, (
        "%s всё ещё разбирает contact_id САМ: %s.\n  форма: %s\n  цена: %s\n"
        "§3.2/§9 п.14: после арки строку не разбирает никто, кроме "
        "`chatter.core.contact_ref` и `Inbound`."
        % (site, ", ".join(raw), PARSE_SITES_12[site]["form"],
           PARSE_SITES_12[site]["why"]))
    assert _imports_owner(rel), (
        "%s больше не режет строку, но и владельца разбора (%s) не импортирует. "
        "Значит место либо перестало иметь дело с contact_id вовсе — и тогда "
        "перепись обязана это ЗАМЕТИТЬ, а не унаследовать, — либо разбор уехал "
        "в третье место, которого в переписи нет."
        % (rel, " / ".join(OWNER_MODULES)))


def test_guard13b_VSTRECHNAYA_polovina_trinadtsatogo_mesta_NET():
    """Сторож 13, ВСТРЕЧНАЯ половина: в `chatter/`, `app/`, `scripts/` не
    появилось ТРИНАДЦАТОГО места разбора — ни одной формой.

    Без неё сторож выше проверяет только двенадцать известных адресов, а
    тринадцатое, дописанное завтра копипастой из двенадцатого, не поймает
    никто: юнит-тест не краснеет на код, который просто не позвали.

    Ищем ПО ФОРМАМ (§8 п.3): `split`, `rsplit`, `partition`, `rpartition`,
    срез, `startswith`/`index`/`find`, регулярка, `LIKE` в SQL. Один шаблон
    уже дважды занизил перепись."""
    hits = _colon_parse_hits()
    offenders = []
    for key, whats in sorted(hits.items()):
        if key in COLON_PARSE_EXEMPT:
            continue
        site = key.split(" || ")[0]
        known = ("ИЗВЕСТНОЕ место переписи, ещё НЕ закрытое (волна %d)"
                 % PARSE_SITES_12[site]["closed"]) if site in PARSE_SITES_12 \
            else "🔴 ТРИНАДЦАТОЕ МЕСТО, переписью 28.08 не учтённое"
        offenders.append("%s — %s [%s]" % (key, known, ", ".join(whats)))
    assert not offenders, (
        "разбор строки по двоеточию найден вне владельца и вне литерального "
        "списка исключений — %d шт.:\n  %s\n"
        "Каждое такое место на форме, которой не знает, вернёт правдоподобный "
        "мусор вместо собеседника либо вместо персоны — молча."
        % (len(offenders), "\n  ".join(offenders)))


def test_guard13_spisok_isklyucheniy_ne_protuh():
    """Встречная половина к САМОМУ СПИСКУ ИСКЛЮЧЕНИЙ.

    Исключение — это дыра в стороже 13, и дыра, за которой никто не следит,
    зарастает сама: строка на функцию, которой в дереве больше нет, ничего не
    освобождает, зато прячет от сканера адрес, по которому завтра ляжет новый
    разбор."""
    dead = []
    for key, reason in sorted(COLON_PARSE_EXEMPT.items()):
        site = key.split(" || ")[0]
        rel, func = site.split("::")
        if not (REPO_ROOT / rel).is_file():
            dead.append("%s — файла нет (освобождено под «%s»)" % (key, reason))
            continue
        if func in ("<module>", "__init__"):
            continue
        if func not in _defines(rel):
            dead.append("%s — функции нет (освобождено под «%s»)" % (key, reason))
    assert not dead, (
        "в списке исключений сторожа 13 %d протухших строк:\n  %s\n"
        "Снимать их обязан тот же коммит, который убрал оттуда разбор."
        % (len(dead), "\n  ".join(dead)))


# ═══ Сторож 14 ══════════════════════════════════════════════════════════════

def test_guard14_perepis_sborki_derzhit_DEVYAT_mest_a_ne_vosem():
    """Сторож 14, пин на число мест СБОРКИ.

    ⚠️ Спека §3.2 называет ВОСЕМЬ (семь f-строк `telethon_run` + одна
    `panels_demo.py:65`). В дереве `9b5ff3f7` их ДЕВЯТЬ: вторая f-строка той
    же `panels_demo.seed` (`add_event(contact_id=f"{500001}:volska")`, сегодня
    :126) в ценз не попала. Это третье занижение переписи подряд по одному
    механизму (§8 п.3), и сторож обязан считать по дереву, а не по числу из
    спеки: иначе он зеленеет на восьми исправленных и девятой оставшейся —
    а девятая после каждого `--seed` возвращает стенду немигрированную форму."""
    assert len(ASSEMBLY_SITES_9) == 7, (
        "в переписи сборки %d ФУНКЦИЙ; ждали 7 функций, дающих 9 f-строк "
        "(две функции содержат по две)." % len(ASSEMBLY_SITES_9))
    hits = _fstring_assembly_hits()
    named = {s: hits.get(s, []) for s in ASSEMBLY_SITES_9}
    total = sum(len(v) for v in named.values())
    assert total == 9, (
        "мест сборки contact_id f-строкой в дереве %d, перепись говорит 9:\n"
        "  %s\nЕсли их стало меньше — арка их закрыла и число обязано ехать "
        "вместе с ней; если больше — перепись занизила В ЧЕТВЁРТЫЙ РАЗ."
        % (total, "\n  ".join("%s: %r" % (k, v) for k, v in sorted(named.items()))))


def test_guard14_sborki_f_strokoy_ne_ostalos_nigde_krome_vladeltsev():
    """Сторож 14: по AST — ни в `chatter/`, ни в `app/`, ни в `scripts/` не
    осталось сборки `contact_id` f-строкой.

    §3.2: «После арки строку не собирает никто, кроме `Inbound.contact_id` и
    `contact_ref`». Сборка — вторая половина того же дефекта, что и разбор:
    девять мест, каждое из которых знает форму наизусть, разъедутся в тот
    день, когда форма изменится, и разъедутся МОЛЧА — f-строка не бросает
    никогда."""
    offenders = []
    for site, whats in sorted(_fstring_assembly_hits().items()):
        rel = site.split("::")[0]
        if rel in ASSEMBLY_OWNERS or site in FSTRING_EXEMPT:
            continue
        known = "известное место §3.2" if site in ASSEMBLY_SITES_9 \
            else "🔴 НОВОЕ место сборки, переписью не учтённое"
        offenders.append("%s — %s [%s]" % (site, known, "; ".join(whats)))
    assert not offenders, (
        "сборка contact_id f-строкой осталась в %d местах вне владельцев "
        "(%s):\n  %s"
        % (len(offenders), ", ".join(ASSEMBLY_OWNERS), "\n  ".join(offenders)))


def test_guard14_VSTRECHNAYA_polovina_vladeltsy_sborki_zarabotali_svoyo():
    """Сторож 14, встречная половина к списку ВЛАДЕЛЬЦЕВ сборки.

    Три способа, которыми освобождение тихо обесценит проверку, и все три
    ловятся здесь: (1) файла в дереве больше нет — строка ничего не
    освобождает, зато скрывает путь; (2) файл есть, но собирать contact_id
    перестал — освобождение не оплачено ничем, и любая f-строка, дописанная в
    него завтра, пройдёт молча; (3) владельцев стало больше двух — «точка
    правды» во множественном числе это ноль точек правды."""
    assert len(ASSEMBLY_OWNERS) == 2, (
        "владельцев сборки стало %d (%s), ждали ровно двух: конверт и "
        "contact_ref (§3.2)." % (len(ASSEMBLY_OWNERS), ", ".join(ASSEMBLY_OWNERS)))
    for rel, reason in sorted(ASSEMBLY_OWNERS.items()):
        assert (REPO_ROOT / rel).is_file(), (
            "%s значится владельцем сборки («%s»), но файла в дереве НЕТ."
            % (rel, reason))
    hits = _fstring_assembly_hits()
    owners_that_build = {s.split("::")[0] for s in hits} & set(ASSEMBLY_OWNERS)
    assert owners_that_build, (
        "ни один из владельцев (%s) не собирает contact_id вовсе. Значит "
        "сборка уехала в третье место, а освобождение висит впустую."
        % ", ".join(ASSEMBLY_OWNERS))


def test_guard14_spisok_isklyucheniy_f_strok_ne_protuh():
    """Встречная половина к списку исключений сторожа 14 — та же причина, что
    и у сторожа 13: незакрытая дыра зарастает сама."""
    dead = []
    for site, reason in sorted(FSTRING_EXEMPT.items()):
        rel, func = site.split("::")
        if not (REPO_ROOT / rel).is_file():
            dead.append("%s — файла нет («%s»)" % (site, reason))
        elif func not in _defines(rel):
            dead.append("%s — функции нет («%s»)" % (site, reason))
    assert not dead, (
        "в списке исключений сторожа 14 %d протухших строк:\n  %s"
        % (len(dead), "\n  ".join(dead)))


# ═══ Сторож 16 (текстовая половина; поведенческая — в test_web_c_four_sites) ═

def test_guard16_slug_hvost_persona_settings_ne_prichesali_zaodno():
    """Сторож 16: `_persona_settings` (`rsplit(":", 1)[-1]`, СЛУГ-хвост) цел.

    §1.2 «что НЕ ломается»: хвост при удлинении головы работает как работал,
    чинить там нечего. Сторож 13 требует убрать разбор из четырёх мест, и
    самая дешёвая правка под это требование — пройти регуляркой по всем
    формам разбора в файле и заменить всё подряд. Тогда слуг перестанет
    находиться, `personas.get(slug, primary)` МОЛЧА свалится на primary, и
    карточка уйдёт владельцу на языке ЧУЖОЙ персоны — без единого исключения
    в логе.

    Принимаем оба честных исхода: живой `rsplit` либо вызов `slug_of` (та же
    операция, вынесенная во владельца). Не принимаем пропажу обоих."""
    rel = "chatter/telethon_run.py"
    tree = _parse(REPO_ROOT / rel)
    target = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_persona_settings"), None)
    assert target is not None, (
        "в %s пропала `_persona_settings` — единственное место, где из "
        "contact_id берётся слуг персоны; её исчезновение обязано быть "
        "замечено, а не проглочено сторожем как «ну и ладно»." % rel)
    ok = any(
        (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and n.func.attr == "rsplit")
        or (isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == "slug_of")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "slug_of")))
        for n in ast.walk(target))
    assert ok, (
        "в `_persona_settings` не осталось ни `rsplit(...)`, ни вызова "
        "`slug_of(...)`: хвост причесали заодно с головой. Карточка уйдёт "
        "владельцу на языке чужой персоны, и в логе не будет ничего.")
