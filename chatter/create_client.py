"""Создание клиента из шаблона (онбординг-дырка №1).

Раньше новый клиент = 4 файла руками, включая 12 обязательных полей timings.
Это был самый долгий шаг онбординга (~20 минут) и главный источник опечаток,
а заодно причина, по которой онбординг нельзя было отдать не-разработчику.

Запуск:
    python -m chatter.create_client acme --persona-name Аня --owner-id Дмитрий

Шаблон намеренно НЕ содержит `funnel_gate` включённым и НЕ содержит блоков
timings/limits: у них есть дефолты в лоадере, а лишние 20 строк в файле
клиента — это 20 строк, которые кто-то однажды поправит не туда.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

FILES = ("persona.md", "knowledge.md", "playbook.md", "settings.yaml")

# ВАЖНО: первая строка — ПРОЗА, не заголовок. Она уходит в ответ лиду на
# «ты бот?» (см. _persona_first_line / honest_disclosure, дырка №0).
_PERSONA = """\
Меня зовут {persona_name}, мне {age}. Я {role}.
Пишу тепло, по-дружески, на «вы», но без официоза — как в переписке с хорошей знакомой.
Сообщения короткие: 1-2 предложения, редко больше.
Эмодзи ставлю изредка, не в каждом сообщении — к месту.

## Чего не делаю
- Не выдумываю фактов, которых нет в базе знаний.
- Не обещаю сроки и цены, которых не знаю — свожу с {owner_ref}.
"""

_KNOWLEDGE = """\
## Услуги
- ЗАПОЛНИТЬ: услуга — цена {currency}, что входит

## Условия
- ЗАПОЛНИТЬ: предоплата, сроки, география

<!-- Эту базу удобнее заливать командой пульта: /knowledge <текст>.
     Формат важен: ## разделы и - пункты считает /config. -->
"""

_PLAYBOOK = """\
## Цель диалога
- Понять задачу лида и её срочность
- Назвать вилку цены из базы знаний
- Свести с {owner_ref}

## Шаги
1. Поздороваться, спросить, что именно нужно
2. Уточнить сроки и бюджет
3. Предложить следующий шаг (созвон/предоплата)

## Стоп-сигналы (эскалировать владельцу)
- Просят скидку сверх вилки
- Жалоба или конфликт
- Вопрос, ответа на который нет в базе знаний
"""

_SETTINGS = """\
# Клиент: {slug}. Минимальный рабочий конфиг — всё остальное имеет дефолты.
model: claude-haiku-4-5          # claude-sonnet-5, если Haiku недостаточно живой
language: {language}
owner_id: "{owner_id}"           # владелец; должен отличаться от persona_name
persona_name: "{persona_name}"
persona_age: {age}
owner_ref: "{owner_ref}"         # уже в нужном падеже: «свяжу вас с ...»

# Brand-safety: валюта клиента + термины, которых в ответе быть не должно.
# Упоминание запрещённого термина → ответ подавляется и уходит владельцу.
currency: "{currency}"
forbidden_terms: {forbidden}
safe_payment_reply: "ЗАПОЛНИТЬ: корректные способы оплаты одной фразой."

# Часы работы персоны (вне их Аня отвечает медленнее). Дефолт 9-22.
work_hours: {{start: 9, end: 22}}

# timings / limits НЕ указаны намеренно — у них рабочие дефолты в лоадере.
# Добавляй сюда только то, что реально хочешь изменить.

telegram:
  allowlist: {allowlist}         # id, которым отвечаем ВСЕГДА (свой тестовый — сюда)
  # denylist: []                 # id, которым не отвечаем никогда
  # funnel_gate включается КОМАНДОЙ пульта: /funnel_gate on
  # (правкой файла — не надо: команда объяснит риск и попросит подтверждение)

control:
  control_bot_token_env: CHATTER_CONTROL_BOT_TOKEN   # ИМЯ env-переменной, НЕ токен
  pairing_code: {pairing}        # deep-link владельцу: t.me/<bot>?start={pairing}
  # owner_chat_id: 123456        # альтернатива: жёсткий id-гейт вместо кода
"""


def render_client(
    *, slug: str, persona_name: str, owner_id: str, language: str = "ru",
    age: int = 26, role: str = "менеджер студии", currency: str = "грн",
    owner_ref: str | None = None, forbidden: list[str] | None = None,
    allowlist: list[int] | None = None,
) -> dict[str, str]:
    """Содержимое 4 файлов. Чистая функция — ни диска, ни сети."""
    owner_ref = owner_ref or "владельцем"
    forbidden = forbidden if forbidden is not None else [
        "рубл", "руб.", "₽", "сбербанк", "тинькофф", "qiwi", "юмани"]
    ctx = dict(slug=slug, persona_name=persona_name, owner_id=owner_id,
               language=language, age=age, role=role, currency=currency,
               owner_ref=owner_ref,
               forbidden="[" + ", ".join(f'"{t}"' for t in forbidden) + "]",
               allowlist="[" + ", ".join(str(int(i)) for i in (allowlist or [])) + "]",
               pairing=f"{slug}-onboard-01")
    return {
        "persona.md": _PERSONA.format(**ctx),
        "knowledge.md": _KNOWLEDGE.format(**ctx),
        "playbook.md": _PLAYBOOK.format(**ctx),
        "settings.yaml": _SETTINGS.format(**ctx),
    }


def create_client(clients_dir: Path | str, *, slug: str, persona_name: str,
                  owner_id: str, **kwargs) -> Path:
    """Создать каталог клиента. Никогда не перезаписывает существующий —
    боевая база знаний дороже удобства."""
    if not SLUG_RE.match(slug or ""):
        raise ValueError(
            f"slug '{slug}': допустимы [a-z0-9_-], начиная с буквы/цифры, до 64 символов")
    if persona_name.strip().casefold() == owner_id.strip().casefold():
        raise ValueError(
            f"persona_name и owner_id должны быть РАЗНЫМИ людьми (оба '{owner_id}')")

    target = Path(clients_dir) / slug
    if target.exists():
        raise FileExistsError(f"клиент '{slug}' уже существует: {target}")

    files = render_client(slug=slug, persona_name=persona_name,
                          owner_id=owner_id, **kwargs)
    target.mkdir(parents=True)
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="chatter.create_client",
                                description="Создать клиента chatter из шаблона")
    p.add_argument("slug")
    p.add_argument("--persona-name", required=True, help="имя персоны, напр. Аня")
    p.add_argument("--owner-id", required=True, help="имя владельца, напр. Дмитрий")
    p.add_argument("--language", default="ru", choices=["ru", "en", "uk"])
    p.add_argument("--age", type=int, default=26)
    p.add_argument("--role", default="менеджер студии")
    p.add_argument("--currency", default="грн")
    p.add_argument("--allowlist", default="",
                    help="id через запятую, которым Аня отвечает ВСЕГДА (свой тестовый)")
    p.add_argument("--owner-ref", default=None,
                    help="как Аня называет владельца в ответе лиду (в падеже)")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    args = p.parse_args(argv)

    try:
        path = create_client(
            args.clients_dir, slug=args.slug, persona_name=args.persona_name,
            owner_id=args.owner_id, language=args.language, age=args.age,
            role=args.role, currency=args.currency, owner_ref=args.owner_ref,
            allowlist=[int(x) for x in args.allowlist.split(",") if x.strip()])
    except (ValueError, FileExistsError) as e:
        print(f"[create_client] {e}", file=sys.stderr)
        return 1

    print(f"[create_client] создан {path}")
    for f in FILES:
        print(f"  - {f}")
    print("\nДальше:")
    print("  1) заполни ЗАПОЛНИТЬ в knowledge.md (или потом командой /knowledge)")
    print("  2) добавь slug в clients/active.yaml — тогда гардиан подхватит клиента")
    print(f"  3) логин: TELETHON_SESSION=.secrets/{args.slug}.session "
          f"python -m chatter.telethon_login")
    print(f"  4) запуск: python -m chatter.telethon_run --personas {args.slug}")
    print("  5) гейт воронки — КОМАНДОЙ пульта: /funnel_gate on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
