# -*- coding: utf-8 -*-
"""Единое меню команд бота — чистый декларативный реестр + рендер.

Модуль НЕ импортирует основной control-файл и НЕ ходит в сеть: держит
упорядоченный реестр категорий/пунктов с ролями/подписями/поведением и
чистые функции рендера inline-клавиатур + построения нативных payload'ов
для ``setMyCommands``. Полностью тестируется на $0.

Три зуба изоляции (описаны в docs/superpowers/plans/2026-07-03-unified-menu.md):
  1. Фильтр по роли на РЕНДЕРЕ (`_visible`) — admin-пункт физически не
     попадает в friend-клавиатуру.
  2. exec идёт через `handle(chat_id, "/cmd")` в основном файле (повторный
     role-гейт `FRIEND_ALLOWED_COMMANDS`) — не здесь.
  3. `lookup_item(cmd, role)` role-checked — friend-запрос admin-команды -> None.

Скрытая категория = категория без единого friend-пункта: видимость выводится
из `item.friend`, а НЕ из хардкод-списка имён категорий. Добавить новую
admin-only категорию = добавить `Category` со всеми `friend=False` — она сама
не покажется friend'у (расширяемо, без правки логики фильтра).
"""
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["admin", "friend"]
Action = Literal["exec", "hint"]


@dataclass(frozen=True)
class MenuItem:
    cmd: str                 # каноническая команда со слешем, напр. "/swapbatch_go"
    label: Optional[str]     # подпись 3-5 слов; None → в кнопке голая команда
    action: Action           # "exec" (сразу handle) | "hint" (показать подсказку)
    friend: bool             # виден ли роли friend
    hint: str = ""           # текст подсказки для action=="hint"


@dataclass(frozen=True)
class Category:
    cat_id: str              # короткий slug для callback, напр. "video"
    title: str               # "🎬 Видео и анимация"
    items: tuple             # tuple[MenuItem, ...]


def _visible(items, role):
    """Пункты категории, видимые роли (первый зуб изоляции)."""
    return [it for it in items if role == "admin" or it.friend]


def _btn_text(it):
    """Текст кнопки: с подписью если label задан, иначе голая команда."""
    return f"{it.cmd} — {it.label}" if it.label else it.cmd


def _mi(cmd, label, action, friend, hint=None):
    """Фабрика пункта. Для action=="hint" без явной подсказки строит дефолт
    (финальную редактуру текстов Даниил сделает после живого теста)."""
    if action == "hint" and not hint:
        base = f"{cmd} — {label}" if label else cmd
        hint = f"{base}\n\nОтправь команду {cmd}, чтобы начать."
    return MenuItem(cmd=cmd, label=label, action=action, friend=friend, hint=hint or "")


# ── Реестр (порядок фиксирован спекой) ─────────────────────────────────────
# F = friend-видимый. Подписи для video/persona/me/photo обязательны; для
# apps/agents/stats/system — голая команда (label=None). Тексты — из спеки
# как есть (Даниил редактирует после живого теста).

_VIDEO = Category("video", "🎬 Видео и анимация", (
    _mi("/swapbatch", "открыть свап-батч ≤100 фото", "hint", True),
    _mi("/swapbatch_source", "задать исходное лицо", "hint", True),
    _mi("/swapbatch_batch", "загрузить пачку фото", "hint", True),
    _mi("/swapbatch_go", "запустить свап", "exec", True),
    _mi("/swapbatch_set_quality", "качество: длительность/fps", "hint", True),
    _mi("/swapbatch_set_prompt", "промт движения для видео", "hint", True),
    _mi("/swapbatch_set_wardrobe", "одежда preserve/safe/spicy", "hint", True),
    _mi("/swapbatch_animate_go", "анимировать swapped-фото", "exec", True),
    _mi("/swapbatch_animate_custom", "свой промт на каждое фото", "hint", True),
    _mi("/swapbatch_status", "статус батча", "exec", True),
    _mi("/swapbatch_cancel", "отменить батч", "exec", True),
    _mi("/animate", "анимировать одно фото", "hint", True),
    _mi("/animate_batch", "анимировать пачку готовых фото", "hint", True),
    _mi("/animate_batch_go", "запустить анимацию пачки", "exec", True),
    _mi("/videoref", "референс-видео → свап+анимация", "hint", True),
))

# Персона: 6 пунктов генерации — friend; 6 пунктов LoRA-управления
# (create/train/status/list/cancel*) — admin-only В МЕНЮ (Block №1 таблица +
# зубы Task 2/Task 3). Они остаются friend-ИСПОЛНЯЕМЫМИ через набранную
# команду (FRIEND_ALLOWED_COMMANDS, money-гейт Арки 1), но меню их не
# показывает — консервативно и money-safe. См. «Решения по ходу».
_PERSONA = Category("persona", "🎭 Персона", (
    _mi("/persona_photo", "фото по обученной LoRA", "hint", True),
    _mi("/persona_video", "фото + видео по LoRA", "hint", True),
    _mi("/persona_video_redo", "переделать видео персоны", "hint", True),
    _mi("/persona_redo", "переделать прошлую генерацию", "hint", True),
    _mi("/persona_engine", "движок видео kling/wan22", "hint", True),
    _mi("/persona_batch", "N фото пачкой", "hint", True),
    _mi("/create_persona", "создать новую персону", "hint", False),
    _mi("/cancel_persona", "отменить создание персоны", "exec", False),
    _mi("/train_lora", "обучить LoRA персоны", "hint", False),
    _mi("/lora_status", "статус обучения LoRA", "hint", False),
    _mi("/list_loras", "список готовых LoRA", "exec", False),
    _mi("/cancel_lora", "отменить обучение LoRA", "hint", False),
))

_ME = Category("me", "🧑 Me-режимы", (
    _mi("/me_swap_photo", "я в фото по промту", "hint", True),
    _mi("/me_swap_video", "вставить моё лицо в видео", "hint", True),
    _mi("/me_into", "вставить меня в чужое фото", "hint", True),
    _mi("/me_as", "я в роли <роль>", "hint", True),
    _mi("/me_in", "я в месте <место>", "hint", True),
    _mi("/me_with", "я с предметом <предмет>", "hint", True),
    _mi("/me_style", "я в стиле <стиль>", "hint", True),
    _mi("/me_roles", "список ролей", "exec", True),
    _mi("/me_places", "список мест", "exec", True),
    _mi("/me_styles", "список стилей", "exec", True),
))

_PHOTO = Category("photo", "🍽 Photo Studio", (
    _mi("/menu_photo", "фото блюда для меню", "hint", True),
    _mi("/social_post", "пост для соцсетей", "hint", True),
    _mi("/menu_book", "меню-книга из блюд", "hint", True),
    _mi("/dish_styles", "стили подачи блюд", "exec", True),
    _mi("/pro_food", "профи фуд-фото", "hint", True),
    _mi("/smart_photo", "умное фото по описанию", "hint", True),
    _mi("/party_promo", "промо вечеринки", "hint", True),
    _mi("/invite_card", "пригласительная открытка", "hint", True),
    _mi("/event_photo", "фото события", "hint", True),
    _mi("/party_themes", "темы вечеринок", "exec", True),
    _mi("/faceswap", "одиночный свап лица", "exec", True),
    _mi("/enhance", "улучшить фото", "exec", True),
))

# apps/agents/stats/system — голые команды (label=None), admin-only
# (кроме /my_stats в stats).
_APPS = Category("apps", "🏗 Приложения и дизайн", (
    _mi("/create_app", None, "hint", False),
    _mi("/create_simple", None, "hint", False),
    _mi("/simple_game", None, "hint", False),
    _mi("/landing", None, "hint", False),
    _mi("/landing_brief", None, "hint", False),
    _mi("/landing_demo", None, "exec", False),
    _mi("/bolt_status", None, "exec", False),
    _mi("/bolt_open", None, "exec", False),
    _mi("/bolt_queue", None, "exec", False),
    _mi("/design", None, "hint", False),
    _mi("/figma_queue", None, "hint", False),
    _mi("/figma_status", None, "exec", False),
    _mi("/figma_clear", None, "exec", False),
))

_AGENTS = Category("agents", "🤖 Агенты", (
    _mi("/agents", None, "exec", False),
    _mi("/mesh", None, "hint", False),
    _mi("/cowork", None, "hint", False),
    _mi("/plan", None, "hint", False),
    _mi("/tasks", None, "exec", False),
    _mi("/task", None, "hint", False),
    _mi("/n8n", None, "hint", False),
    _mi("/brain", None, "hint", False),
    _mi("/research", None, "hint", False),
    _mi("/engineer", None, "hint", False),
    _mi("/table", None, "hint", False),
    _mi("/gen", None, "hint", False),
    _mi("/job", None, "hint", False),
))

# stats: friend видит ТОЛЬКО /my_stats (зуб test_stats_category_friend_subset).
_STATS = Category("stats", "📊 Статистика и деньги", (
    _mi("/my_stats", None, "exec", True),
    _mi("/stats", None, "exec", False),
    _mi("/costs", None, "exec", False),
    _mi("/status", None, "exec", False),
    _mi("/history", None, "hint", False),
    _mi("/admin_users", None, "exec", False),
    _mi("/admin_setlimit", None, "hint", False),
    _mi("/admin_resetlimit", None, "hint", False),
    _mi("/admin_activity", None, "exec", False),
))

_SYSTEM = Category("system", "⚙️ Система", (
    _mi("/smart_health", None, "exec", False),
    _mi("/debug_health", None, "exec", False),
    _mi("/diag", None, "exec", False),
    _mi("/selfcheck", None, "exec", False),
    _mi("/logs", None, "hint", False),
    _mi("/errors", None, "hint", False),
    _mi("/restart_backend", None, "exec", False),
    _mi("/restart_bot", None, "exec", False),
    _mi("/mode", None, "hint", False),
    _mi("/cancel", None, "exec", False),
    _mi("/clear", None, "exec", False),
    _mi("/memory_stats", None, "exec", False),
    _mi("/night_status", None, "exec", False),
    _mi("/night_now", None, "exec", False),
    _mi("/brief", None, "hint", False),
    _mi("/remind", None, "hint", False),
    _mi("/schedule", None, "hint", False),
    _mi("/improve", None, "hint", False),
    _mi("/capabilities", None, "exec", False),
))

# 🔧 Наблюдение (observation-console): 4 read-only admin-only команды. Все
# friend=False → категория авто-скрыта от friend (как system/agents). Пункты
# exec (сразу выполняют read-only команду через handle()).
_OBSERVE = Category("observe", "🔧 Наблюдение", (
    _mi("/git_status", "статус git", "exec", False),
    _mi("/regress", "прогон тестов", "exec", False),
    _mi("/logs_tail", "хвост логов", "exec", False),
    _mi("/health", "здоровье систем", "exec", False),
))

MENU = [_VIDEO, _PERSONA, _ME, _PHOTO, _APPS, _AGENTS, _STATS, _SYSTEM, _OBSERVE]


# ── Рендер (чистые функции: (текст, inline_keyboard)) ──────────────────────
def render_root(role):
    """Корень каталога: по одной кнопке-строке на видимую роли категорию.

    Категория видна, если у неё есть ≥1 видимый роли пункт (первый зуб на
    уровне категорий — admin-only категория не отрисуется friend'у)."""
    rows = []
    for cat in MENU:
        if _visible(cat.items, role):
            rows.append([{"text": cat.title, "callback_data": f"menu:cat:{cat.cat_id}"}])
    text = "☰ Меню — выбери категорию:" if rows else "Нет доступных команд."
    return text, rows


def render_category(cat_id, role):
    """Пункты категории для роли + кнопка «⬅️ Назад».

    Возвращает None если категория неизвестна ИЛИ недоступна роли (нет ни
    одного видимого пункта) — вызывающий покажет «🚫»."""
    cat = next((c for c in MENU if c.cat_id == cat_id), None)
    if cat is None:
        return None
    vis = _visible(cat.items, role)
    if not vis:
        return None
    rows = [[{"text": _btn_text(it), "callback_data": f"menu:x:{it.cmd.lstrip('/')}"}]
            for it in vis]
    rows.append([{"text": "⬅️ Назад", "callback_data": "menu:root"}])
    return cat.title, rows


def lookup_item(cmd, role):
    """Найти пункт по команде, role-checked (третий зуб изоляции).

    friend-запрос admin-команды -> None (вызывающий покажет «🚫»). Принимает
    команду со слешем и без."""
    norm = "/" + cmd.lstrip("/")
    for cat in MENU:
        for it in cat.items:
            if it.cmd == norm and (role == "admin" or it.friend):
                return it
    return None


# ── Нативное меню ☰ (setMyCommands) ────────────────────────────────────────
# (имя_без_слеша, описание). Топ ~10-15 на scope. default=friend-список (его
# видят все, включая новых friend); admin-scope поверх default по chat_id.
# Тексты — черновик из спеки (Даниил редактирует после живого теста).
NATIVE_ADMIN_COMMANDS = [
    ("menu", "Каталог всех команд"),
    ("status", "Статус AI-провайдеров"),
    ("stats", "Статистика за сегодня"),
    ("costs", "Траты за сегодня"),
    ("smart_health", "Здоровье систем"),
    ("swapbatch", "Пакетный свап лиц"),
    ("animate", "Анимировать фото"),
    ("videoref", "Референс-видео → свап"),
    ("persona_photo", "Фото по LoRA-персоне"),
    ("persona_video", "Видео по LoRA-персоне"),
    ("agents", "Статус агентов"),
    ("tasks", "Задачи Vizir"),
    ("logs", "Читать логи"),
    ("git_status", "Статус git"),
    ("regress", "Прогон тестов"),
    ("logs_tail", "Хвост логов"),
    ("health", "Здоровье систем"),
    ("my_stats", "Личная статистика"),
    ("help", "Справка"),
]

NATIVE_FRIEND_COMMANDS = [
    ("menu", "Меню команд"),
    ("swapbatch_source", "Задать исходное лицо"),
    ("animate", "Анимировать фото"),
    ("videoref", "Референс-видео → свап"),
    ("persona_photo", "Фото по персоне"),
    ("persona_video", "Видео по персоне"),
    ("my_stats", "Моя статистика и лимит"),
    ("help", "Справка"),
]


def _cmds(pairs):
    return [{"command": n, "description": d} for n, d in pairs]


def build_native_payloads(admin_chat_id):
    """Payload'ы для setMyCommands: default=friend-список, admin-scope по chat.

    Зуб нативного слоя: default (его видят все) содержит только friend-команды;
    admin-команды — лишь в scope конкретного admin chat_id."""
    return {
        "default": {"commands": _cmds(NATIVE_FRIEND_COMMANDS),
                    "scope": {"type": "default"}},
        "admin": {"commands": _cmds(NATIVE_ADMIN_COMMANDS),
                  "scope": {"type": "chat", "chat_id": str(admin_chat_id)}},
    }
