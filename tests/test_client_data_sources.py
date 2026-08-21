# -*- coding: utf-8 -*-
"""DEV-46: у бэкапа появился ВТОРОЙ корень обхода — клиентский набор.

ЧТО МЕНЯЕТСЯ. `discover_backup_files` устроен вокруг ОДНОГО корня (`state/`),
и обе пропажи — история воронки `.secrets/<slug>.db` и платёжные реквизиты
`chatter/clients/<slug>/requisites.yaml` — лежат вне него (§1.3). Их не
«исключили» — их не могло быть видно по построению обхода. Правка добавляет
корень; корень собирается ПО СЛАГАМ ИЗ РЕЕСТРА, а не по одному примеру.

ГЛАВНЫЙ РИСК НАЗВАН В СПЕКЕ ПРЯМО (§2.3): новый корень втянет соседей по
каталогу МОЛЧА. Рядом с базой в `.secrets/` лежат `<slug>.session`,
`*.session.enc`, `entropy.bin`, рядом с корнем репозитория — `.env`. Разница
между «данные клиента» и «полный доступ к аккаунту клиента» здесь ровно в
хвосте имени файла, и один широкий глоб (`.secrets/*`) стирает её целиком.
Поэтому сторож в ОБЕ стороны: нужное доехало И лишнее не доехало. Вторая
половина важнее первой: не доехавшую базу заметят при восстановлении, а
уехавшую сессию не заметит никто.

ТА ЖЕ МЫСЛЬ, ЧТО В `test_state_backup_evidence_registry.py`, НА НОВОМ КОРНЕ.
Реестр запрещённого ниже — ЛИТЕРАЛЬНЫЙ список конкретных экземпляров с
причиной словами у каждой записи, а не выборка из `sb.FORBIDDEN_PATTERNS`.
Реестр, выведенный из кода, согласен с кодом по определению и промолчит ровно
там, где код забыл.

ЧЕГО ЗДЕСЬ НЕТ. Ни сети, ни живых файлов: все деревья строятся в `tmp_path`,
живой `.secrets/` не читается ни разу. Загрузки в R2 на этом шаге нет вовсе
(шифрование — шаг 4), поэтому сторожа §7 п. 4 (открытый текст наружу) и §9.1
(слаг в ключе объекта) живут в других файлах.

ГДЕ СПЕКА МОЛЧИТ — ВЫБРАН СТРОГИЙ ВАРИАНТ, и это сказано в докстроке теста.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from app.services import state_backup as sb

# ── Синтетические слаги ──────────────────────────────────────────────────────
# НЕ живые (`volska`/`yarina`) намеренно: сторож не имеет права зависеть от
# того, кого сегодня включили в боевом реестре, и не должен давать повода
# заглянуть в живой `.secrets/`.
ALPHA = "alpha"
BETA = "beta"
SLUGS: tuple[str, ...] = (ALPHA, BETA)

REPO_DIRNAME = "repo"


# ── ЧТО ОБЯЗАНО ДОЕХАТЬ ──────────────────────────────────────────────────────
# Экземпляры, а не глобы. Причина словами — чтобы через полгода запись не
# выглядела случайной строкой.
MUST_TRAVEL: tuple[tuple[str, str], ...] = tuple(
    item
    for slug in SLUGS
    for item in (
        (f".secrets/{slug}.db",
         "история воронки: переписка с лидами, состояние обязательств, "
         "платежи; заполняется только вперёд и восстановить её неоткуда — "
         "ни из git, ни из Telegram"),
        (f"chatter/clients/{slug}/requisites.yaml",
         "платёжные реквизиты, которые дал сам клиент; gitignored намеренно, "
         "второй копии не существует ни в одном из трёх хранилищ"),
    )
)


# ── ЧТО УЕЗЖАТЬ НЕ ИМЕЕТ ПРАВА ───────────────────────────────────────────────
# Соседи по каталогу. Каждая запись — ровно тот файл, который утащил бы
# широкий глоб, и то, чем оборачивается его утечка.
MUST_NOT_TRAVEL: tuple[tuple[str, str], ...] = tuple(
    item
    for slug in SLUGS
    for item in (
        (f".secrets/{slug}.session",
         "session-файл Telethon = полный доступ к аккаунту клиента без "
         "пароля, немедленно и молча; в чужом хранилище это выданный ключ"),
        (f".secrets/{slug}.session.enc",
         "та же сессия под DPAPI: на машине владельца открывается штатно, "
         "то есть утечка отличается от plaintext только лишним шагом"),
        (f".secrets/{slug}.db-journal",
         "хвост незавершённой транзакции SQLite: не данные и не бэкап, но "
         "содержит те же строки переписки и уезжает мимо всякого шифрования "
         "содержимого базы"),
        (f".secrets/{slug}.db.pre-3a-123.bak",
         "страховка миграции схемы, которую db.py кладёт рядом с оригиналом; "
         "это старый снимок ТОЙ ЖЕ базы — уехав, он удваивает поверхность и "
         "подсовывает восстановлению вчерашнюю схему"),
    )
) + (
    (".secrets/entropy.bin",
     "материал опознания: соль, которой открываются зашифрованные сессии; "
     "уехав рядом с ними, обесценивает само шифрование"),
    (".env",
     "все живые ключи разом — Anthropic, R2, токены ботов; одна строка "
     "оттуда оплачивается с карты владельца"),
    (".env.enc",
     "тот же .env под DPAPI; хранить его в чужом бакете — значит ждать "
     "только доступа к машине, а не к секретам"),
)


# ── ПРИШПИЛЕННЫЙ СПИСОК ЗАПРЕЩЁННОГО ─────────────────────────────────────────
# Литеральная копия. Не выводить из `sb.FORBIDDEN_PATTERNS` ни при каких
# обстоятельствах — то же правило, что у `PINNED_PATTERNS` в DEV-44
# (`test_state_backup_evidence_registry.py`): копия, посчитанная из кода, равна
# оригиналу по определению и не краснеет никогда. Смысл пришпиливания не в
# том, чтобы запретить правку, а в том, чтобы правка была обязана пройти МИМО
# реестра причин выше: сторож не может знать, что новый файл на диске — это
# доступ к аккаунту, но может не дать сузить сито молча.
PINNED_FORBIDDEN: tuple[str, ...] = (
    ".secrets/*.session",
    ".secrets/*.session.enc",
    ".secrets/*.enc",
    ".secrets/entropy.bin",
    ".secrets/*.bak",
    ".secrets/*.db-journal",
    ".secrets/*.db-wal",
    ".secrets/*.db-shm",
    ".env",
    ".env.enc",
    ".env.runpod",
    "state/connect/*.session",
    "state/connect/secrets_bundle.zip",
    "state/api_keys.json",
    "state/ig_accounts.json",
    "state/google_oauth_token.json",
)


# ── дерево в tmp_path ────────────────────────────────────────────────────────
def _registry_text(clients: dict[str, dict]) -> str:
    """YAML реестра в той же форме, что `chatter/clients/registry.yaml`."""
    lines = ["clients:"]
    for slug, cfg in clients.items():
        lines.append(f"  {slug}:")
        lines.append(f"    enabled: {'true' if cfg.get('enabled', True) else 'false'}")
        lines.append(f"    personas: [{slug}]")
        lines.append(f"    session: {cfg.get('session', f'.secrets/{slug}.session')}")
        lines.append(f"    db: {cfg.get('db', f'.secrets/{slug}.db')}")
    return "\n".join(lines) + "\n"


def _two_enabled() -> dict[str, dict]:
    return {slug: {"enabled": True} for slug in SLUGS}


def _repo_tree(
    tmp_path: Path,
    registry_text: str,
    *,
    skip: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> Path:
    """Корень репозитория, где нужное и запрещённое лежат ВПЕРЕМЕШКУ.

    Так же, как `_state_tree` в `test_state_backup_evidence_registry.py`, и
    так же, как на живом диске: `.secrets/alpha.db` и `.secrets/alpha.session`
    отличаются одним хвостом имени и лежат в одном каталоге.
    """
    root = tmp_path / REPO_DIRNAME
    rels = [rel for rel, _why in MUST_TRAVEL + MUST_NOT_TRAVEL]
    rels.extend(extra)
    for rel in rels:
        if rel in skip:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"содержимое {rel}", encoding="utf-8")
    reg = root / "chatter" / "clients" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    # Реестр кладём НА ДИСК и передаём ТЕКСТОМ одновременно: сторож не должен
    # зависеть от того, читает реализация файл или принимает текст.
    reg.write_text(registry_text, encoding="utf-8")
    return root


def _rel(path: Path, root: Path) -> str:
    """Путь относительно корня в posix-виде; вне корня — абсолютом (это тоже улика)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _norm(path: Path) -> str:
    """Ключ сравнения по РЕАЛЬНОМУ пути, а не по объекту `Path`."""
    return os.path.normcase(str(Path(path).resolve()))


def _picked(sets, root: Path) -> list[str]:
    """Всё, что набор реально увозит. СПИСОК, а не множество: повторы значимы."""
    out: list[str] = []
    for s in sets:
        for p in (s.db, s.requisites):
            if p is not None:
                out.append(_rel(p, root))
    return out


def _by_slug(sets, slug: str):
    for s in sets:
        if s.slug == slug:
            return s
    return None


# ── 1. нужное доезжает ───────────────────────────────────────────────────────
@pytest.mark.parametrize("slug", SLUGS)
def test_every_enabled_client_set_reaches_the_backup(tmp_path, slug):
    """Без него молча проехало бы: набор собран по ОДНОМУ примеру.

    Реализация, написанная под живой реестр, легко оказывается верной для
    первого клиента и слепой для второго (пин `volska` на пути demo-аккаунта
    делает первый пример нетипичным). Реестр здесь с ДВУМЯ включёнными
    клиентами, параметризация — по слагам, поэтому падение называет виноватого.
    """
    root = _repo_tree(tmp_path, _registry_text(_two_enabled()))
    sets = sb.client_sets(root, registry_text=_registry_text(_two_enabled()))

    cs = _by_slug(sets, slug)
    assert cs is not None, (
        f"клиента `{slug}` нет в наборе вовсе, хотя в реестре он enabled: true.\n"
        f"Слаги набора: {sorted(s.slug for s in sets)}.\n"
        f"Похоже, источник берётся не из реестра, а из одного примера."
    )

    db_rel = f".secrets/{slug}.db"
    req_rel = f"chatter/clients/{slug}/requisites.yaml"
    picked = _picked(sets, root)

    assert cs.db is not None and _rel(cs.db, root) == db_rel, (
        f"история воронки `{db_rel}` НЕ уезжает в бэкап.\n"
        f"Чем оборачивается потеря: восстановить её неоткуда — ни из git "
        f"(бинарь, gitignored), ни из Telegram, ни из бандла секретов; "
        f"пропажа обнаружится не в момент потери, а когда клиент спросит "
        f"про май.\n"
        f"В наборе `{slug}` db = {cs.db!r}; увезено всего: {picked}"
    )
    assert cs.requisites is not None and _rel(cs.requisites, root) == req_rel, (
        f"реквизиты `{req_rel}` НЕ уезжают в бэкап.\n"
        f"Чем оборачивается потеря: платёжных данных клиента нет ни в git "
        f"(gitignored намеренно), ни в бандле (слова `requisites` там нет ни "
        f"разу) — второй копии не существует.\n"
        f"В наборе `{slug}` requisites = {cs.requisites!r}; увезено всего: {picked}"
    )


# ── 2. лишнее не доезжает ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "rel,why", MUST_NOT_TRAVEL, ids=[e[0] for e in MUST_NOT_TRAVEL]
)
def test_no_neighbour_from_the_forbidden_registry_reaches_the_backup(tmp_path, rel, why):
    """Без него молча проехало бы: новый корень утащил соседей по каталогу.

    Это главный риск правки, названный в §2.3 прямо. Широкий глоб `.secrets/*`
    проходит первую половину сторожа (база доехала!) и увозит вместе с ней
    полный доступ к аккаунту клиента. Реестр запрещённого — литеральный, у
    каждой записи причина словами.
    """
    root = _repo_tree(tmp_path, _registry_text(_two_enabled()))
    sets = sb.client_sets(root, registry_text=_registry_text(_two_enabled()))
    picked = _picked(sets, root)

    assert rel not in picked, (
        f"`{rel}` УЕЗЖАЕТ в бэкап, а не имеет права: {why}.\n"
        f"Скорее всего источник расширен до каталога (`.secrets/*`) вместо "
        f"точного пути базы из реестра, либо сито `is_forbidden` не позвали "
        f"на отобранное.\n"
        f"Увезено целиком: {picked}"
    )


# ── 3. сито срабатывает, даже если источник ошибся ───────────────────────────
@pytest.mark.parametrize(
    "bad_db,needle",
    [
        (f".secrets/{ALPHA}.session", f"{ALPHA}.session"),
        (".secrets/entropy.bin", "entropy.bin"),
    ],
    ids=["db указывает на session", "db указывает на entropy.bin"],
)
def test_a_mistyped_source_raises_forbidden_travel_and_names_the_file(
    tmp_path, bad_db, needle
):
    """Без него молча проехало бы: тихая фильтрация вместо громкого отказа.

    Человек ошибся строкой в реестре — `db:` показывает на сессию. Реализация,
    которая просто вычёркивает запрещённое и возвращает набор без базы, ведёт
    себя ровно как исправная: бэкап «прошёл», клиент «в наборе», а истории
    воронки в нём нет, и узнается это при восстановлении через полгода.
    Требуем ИСКЛЮЧЕНИЕ с именем файла в сообщении.

    СТРОГИЙ ВАРИАНТ ТАМ, ГДЕ СПЕКА МОЛЧИТ: спека говорит «фейл-клоуз и
    громко», но не говорит, падает ли весь сбор или только этот клиент. Здесь
    требуется падение сбора целиком — ошибка в источнике секретов не имеет
    права деградировать до предупреждения в логе (DEV-18: не глотать).
    """
    clients = _two_enabled()
    clients[ALPHA] = {"enabled": True, "db": bad_db}
    text = _registry_text(clients)
    root = _repo_tree(tmp_path, text)

    with pytest.raises(sb.ForbiddenTravel) as exc:
        sb.client_sets(root, registry_text=text)

    assert needle in str(exc.value), (
        f"`ForbiddenTravel` поднято, но сообщение не называет файл `{needle}`.\n"
        f"Читающий лог обязан узнать, ЧТО именно чуть не уехало, — иначе "
        f"фейл-клоуз превращается в загадку.\n"
        f"Сообщение: {str(exc.value)!r}"
    )


# ── 4. `*` не пересекает `/` ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "rel,expected,why",
    [
        (f".secrets/{ALPHA}.session", True,
         "прямой сосед базы: ровно то, ради чего сито написано"),
        (f".secrets/sub/{ALPHA}.session", False,
         "`*` не имеет права пересекать `/`: наивный fnmatch по всему пути "
         "сказал бы True и запретил бы то, чего шаблон не называл"),
        (f".secrets/{ALPHA}.session.enc", True,
         "та же сессия под DPAPI"),
        (f".secrets/{ALPHA}.db", False,
         "ЭТО ТО, ЧТО ОБЯЗАНО ЕХАТЬ. Сито, запретившее базу, ломает всю "
         "правку и выглядит при этом как аккуратная осторожность"),
        (f".secrets/{ALPHA}.db-wal", True,
         "хвост WAL: те же строки переписки мимо шифрования содержимого"),
        (f"chatter/clients/{ALPHA}/requisites.yaml", False,
         "второй обязательный файл набора"),
        (".env", True, "все живые ключи разом"),
        (".env.runpod", True, "ключи арендованного GPU-хоста"),
        ("state/connect/x.session", True,
         "session-файл Telethon в старом корне — тот же класс"),
        ("state/connect/sub/x.session", False,
         "`*` снова не пересекает `/`, теперь во вложенном шаблоне"),
    ],
    ids=[
        "session рядом с базой -> True",
        "session на уровень глубже -> False",
        "session.enc -> True",
        "САМА БАЗА -> False",
        "db-wal -> True",
        "requisites.yaml -> False",
        ".env -> True",
        ".env.runpod -> True",
        "state/connect/*.session -> True",
        "state/connect глубже -> False",
    ],
)
def test_the_star_never_crosses_a_slash(rel, expected, why):
    """Без него молча проехало бы: сито через `fnmatch` по всему пути.

    `fnmatch` — первая мысль любого автора, и его `*` съедает `/`. Ошибка
    двусторонняя и обе стороны дорогие: запретить `.secrets/sub/x.session`
    — это лишний запрет, который никто не заметит, а разрешить `.secrets/x.db`
    было бы прямым провалом правки. Сегменты сравниваются посегментно.
    """
    got = sb.is_forbidden(rel)
    assert bool(got) == expected, (
        f"`is_forbidden({rel!r})` вернуло {got!r}, ожидалось {expected!r}.\n"
        f"Почему так: {why}.\n"
        f"`*` обязан совпадать внутри ОДНОГО сегмента пути — шаблоны из "
        f"FORBIDDEN_PATTERNS написаны посегментно."
    )


# ── 5. дедупликация общей базы ───────────────────────────────────────────────
def test_a_db_shared_by_two_enabled_clients_travels_exactly_once(tmp_path):
    """Без него молча проехало бы: один файл увезён и посчитан дважды.

    Живая раскладка именно такая: `volska` пиннится на `.secrets/demo.db`, и
    запись `demo` показывает туда же. Дедуп по объекту `Path` тут не работает
    — `Path('.secrets/demo.db')` и `Path('.secrets\\\\demo.db')` не равны, а файл
    один. Двойной проезд это не только лишний трафик: манифест насчитает два
    объекта на один файл, а восстановление получит два кандидата на одну базу.

    СТРОГИЙ ВАРИАНТ ТАМ, ГДЕ СПЕКА МОЛЧИТ: сколько при этом получится
    `ClientSet`-ов (один на пару или по одному на слаг), контракт не говорит,
    и тест этого НЕ требует. Требуется одно: файл в увезённом ровно один раз.
    """
    shared = ".secrets/shared.db"
    clients = {
        ALPHA: {"enabled": True, "db": shared},
        BETA: {"enabled": True, "db": shared},
    }
    text = _registry_text(clients)
    root = _repo_tree(tmp_path, text, extra=(shared,))

    sets = sb.client_sets(root, registry_text=text)
    dbs = [_norm(s.db) for s in sets if s.db is not None]
    target = _norm(root / shared)
    hits = [d for d in dbs if d == target]

    assert len(hits) == 1, (
        f"общая база `{shared}` увозится {len(hits)} раз(а), а обязана один.\n"
        f"Считать надо по НОРМАЛИЗОВАННОМУ реальному пути, а не по объекту "
        f"`Path`: два слага показывают на один файл (сегодня это volska и "
        f"demo на `.secrets/demo.db`).\n"
        f"Базы набора: {dbs}"
    )


# ── 6. выключенный клиент не едет ────────────────────────────────────────────
def test_a_disabled_client_travels_nothing_and_an_enabled_one_travels(tmp_path):
    """Без него молча проехало бы: база выключенного клиента в чужом бакете.

    `enabled: false` — это не «клиент на паузе», это «его данными мы больше не
    распоряжаемся»: демо закончилось, договорённости нет, а переписка живого
    человека продолжает ежедневно уезжать наружу.

    Обратная сторона В ТОМ ЖЕ ТЕСТЕ обязательна: реализация, возвращающая
    пустоту всегда, прошла бы первую половину идеально. Поэтому тот же клиент
    включается, и от него требуется проезд.
    """
    off = {ALPHA: {"enabled": False}, BETA: {"enabled": True}}
    text_off = _registry_text(off)
    root = _repo_tree(tmp_path, text_off)

    picked_off = _picked(sb.client_sets(root, registry_text=text_off), root)
    for rel in (f".secrets/{ALPHA}.db", f"chatter/clients/{ALPHA}/requisites.yaml"):
        assert rel not in picked_off, (
            f"`{rel}` уезжает, хотя клиент `{ALPHA}` выключен (enabled: false).\n"
            f"Выключенный клиент — это чаще всего закончившееся демо: его "
            f"данные не имеют права продолжать ежедневно уходить в чужое "
            f"хранилище.\n"
            f"Увезено: {picked_off}"
        )

    text_on = _registry_text(_two_enabled())
    picked_on = _picked(sb.client_sets(root, registry_text=text_on), root)
    for rel in (f".secrets/{ALPHA}.db", f"chatter/clients/{ALPHA}/requisites.yaml"):
        assert rel in picked_on, (
            f"`{rel}` НЕ уезжает даже после включения клиента `{ALPHA}`.\n"
            f"Значит первая половина теста зелена на пустом наборе, а не "
            f"благодаря флагу enabled.\n"
            f"Увезено: {picked_on}"
        )


# ── 7. «файла нет» ≠ «файл не искали» ────────────────────────────────────────
def test_a_missing_file_is_named_in_missing_not_dropped_silently(tmp_path):
    """Без него молча проехало бы: «0 файлов» неотличимо от «файлов нет» (§7 п. 5).

    Реквизиты не завели, переименовали каталог клиента, слаг написали с
    опечаткой — во всех трёх случаях исправный бэкап выглядит одинаково
    зелёным, а реквизитов в нём нет. Молчаливый пропуск ровно это и делает:
    `discover_backup_files` сегодня пропускает ненайденное намеренно, и для
    свежей установки это правильно, а для клиента из реестра — нет.

    Зеркало в том же тесте: когда файл на месте, `missing` пуст, иначе сторож
    зелен на реализации «всегда жалуйся».
    """
    req_rel = f"chatter/clients/{ALPHA}/requisites.yaml"
    text = _registry_text(_two_enabled())
    root = _repo_tree(tmp_path, text, skip=(req_rel,))

    cs = _by_slug(sb.client_sets(root, registry_text=text), ALPHA)
    assert cs is not None, f"клиента `{ALPHA}` нет в наборе вовсе"
    assert cs.requisites is None, (
        f"`requisites` указывает на {cs.requisites!r}, хотя файла "
        f"`{req_rel}` на диске нет"
    )
    assert req_rel in cs.missing, (
        f"файла `{req_rel}` нет на диске, и об этом НИКТО не сказал.\n"
        f"`missing` = {cs.missing!r}.\n"
        f"«0 файлов» и «файлов нет» обязаны быть отличимы: иначе не заведённые "
        f"реквизиты и исправный бэкап выглядят одинаково."
    )

    full = _repo_tree(tmp_path / "full", text)
    for cs2 in sb.client_sets(full, registry_text=text):
        assert not cs2.missing, (
            f"у клиента `{cs2.slug}` `missing` = {cs2.missing!r}, хотя ВСЕ "
            f"файлы на месте.\n"
            f"Реализация «всегда жалуйся» зеленит первую половину теста и "
            f"обесценивает сам сигнал."
        )


# ── 8. сирота названа вслух ──────────────────────────────────────────────────
def test_an_orphan_db_is_named_aloud_and_never_carried_silently(tmp_path):
    """Без него молча проехало бы: база без хозяина — увезена или забыта.

    Обе половины одинаково плохи и обе тихие. Увезти базу, не принадлежащую ни
    одному слагу реестра, — значит копировать наружу данные, о которых никто
    не решал; молча пропустить — значит потерять историю клиента, которого
    забыли (или ещё не успели) вписать в реестр. Правильный ответ — назвать
    вслух и не везти.
    """
    orphan_rel = ".secrets/ничей.db"
    text = _registry_text(_two_enabled())
    root = _repo_tree(tmp_path, text, extra=(orphan_rel,))

    sets = sb.client_sets(root, registry_text=text)
    orphans = {_norm(p) for p in sb.orphan_client_dbs(root, sets)}
    picked = _picked(sets, root)

    assert _norm(root / orphan_rel) in orphans, (
        f"`{orphan_rel}` не принадлежит ни одному слагу реестра и НЕ назван "
        f"сиротой.\n"
        f"Названо сиротами: {sorted(orphans)}.\n"
        f"Это история клиента, которого забыли вписать; тихий пропуск теряет "
        f"её так же надёжно, как отсутствие бэкапа вообще."
    )
    assert orphan_rel not in picked, (
        f"`{orphan_rel}` УВЕЗЕН, хотя хозяина в реестре у него нет.\n"
        f"Наружу копируются данные, о которых решения не принимали.\n"
        f"Увезено: {picked}"
    )
    for slug in SLUGS:
        owned = _norm(root / f".secrets/{slug}.db")
        assert owned not in orphans, (
            f"`.secrets/{slug}.db` объявлена сиротой, хотя её хозяин `{slug}` "
            f"стоит в реестре.\n"
            f"Похоже, сиротой зовётся просто любой `.secrets/*.db`, и тогда "
            f"первая половина теста зелена по построению."
        )


# ── 9. список запрещённого пришпилен ─────────────────────────────────────────
def test_forbidden_patterns_is_literal_and_pinned_in_both_directions():
    """Без него молча проехало бы: сито сузили, и никто не прошёл мимо причин.

    Мысль DEV-44 своими словами: сторож не умеет узнать, что новый файл на
    диске — это доступ к аккаунту. Зато он умеет не дать изменить сито молча.
    Любая правка `FORBIDDEN_PATTERNS` красит этот тест, и автор, поправляя
    литеральную копию, обязан пройти мимо `MUST_NOT_TRAVEL` выше и ответить:
    появился ли сосед, чья утечка = доступ к чужому аккаунту, — и не сузили ли
    шаблон так, что прежний сосед перестал ловиться.

    Копия ЛИТЕРАЛЬНАЯ: любое выражение через `sb.FORBIDDEN_PATTERNS` равно
    оригиналу по определению и не краснеет никогда.

    СТРОГИЙ ВАРИАНТ: сверка тождеством, а не включением, — поэтому красит и
    перестановка. Список из шестнадцати строк редактируют не каждый день, а
    «включение» пропустило бы удаление шаблона в одну сторону.
    """
    assert sb.FORBIDDEN_PATTERNS, (
        "`FORBIDDEN_PATTERNS` пуст. Пустое сито — это отсутствие сита: "
        "`assert_not_forbidden` пропустит и сессию, и `.env`, и при этом "
        "останется зелёным."
    )

    pinned, live = set(PINNED_FORBIDDEN), set(sb.FORBIDDEN_PATTERNS)
    lost = sorted(pinned - live)
    added = sorted(live - pinned)
    assert tuple(sb.FORBIDDEN_PATTERNS) == PINNED_FORBIDDEN, (
        "список запрещённого изменился.\n"
        "Это не ошибка сама по себе — но прежде чем поправить "
        "PINNED_FORBIDDEN, ответь на два вопроса DEV-46:\n"
        "  1) появился ли рядом с базой сосед, чья утечка = полный доступ к "
        "аккаунту клиента? Тогда допиши его экземпляр в MUST_NOT_TRAVEL;\n"
        "  2) не сузили ли шаблон так, что прежний сосед перестал ловиться?\n"
        f"пропало из списка: {lost or '—'}\n"
        f"добавилось:        {added or '—'}\n"
        f"было:  {PINNED_FORBIDDEN}\n"
        f"стало: {tuple(sb.FORBIDDEN_PATTERNS)}"
    )


# ── 10. ОДНА ротация, а не две ───────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS: tuple[str, ...] = ("app", "scripts", "tools")
ROTATION_CALLEE = "rotate_old_backups"
ROTATION_WRAPPER = "rotate_all_backups"
ROTATION_HOME = "app/services/state_backup.py"


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _collect_calls(node: ast.AST, rel: str, fn: str | None, out: list) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect_calls(child, rel, child.name, out)
            continue
        if isinstance(child, ast.Call) and _called_name(child) == ROTATION_CALLEE:
            out.append((rel, child.lineno, fn))
        _collect_calls(child, rel, fn, out)


def _rotation_call_sites(root: Path, dirs: tuple[str, ...] = SCAN_DIRS) -> list[tuple]:
    """Все вызовы `rotate_old_backups(` в дереве: (путь, строка, чья функция).

    Разбор через AST, а не текстом. Разница не косметическая: закомментированный
    вызов и строка `"rotate_old_backups"` в докстроке текстовым поиском
    неотличимы от живого вызова — сторож на тексте красен вечно и потому
    выключается, а не чинится.

    `utf-8-sig`: 138 файлов дерева лежат с BOM, и `utf-8` спотыкается на них
    синтаксической ошибкой — слепота на каждом шестом файле.

    Файл, который AST не разбирает (сегодня такой один — старый
    `tools/jarvis_n8n_full_autonomy_v3_1.py` со сломанными скобками), — это
    слепая зона. Молчим о нём ТОЛЬКО если имени вызова нет в тексте вовсе;
    иначе он попадает в улики отдельной строкой, а не исчезает.
    """
    sites: list[tuple] = []
    for d in dirs:
        base = Path(root) / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(Path(root)).as_posix()
            try:
                text = p.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                if ROTATION_CALLEE in text:
                    sites.append((rel, 0, "<файл не разбирается AST>"))
                continue
            _collect_calls(tree, rel, None, sites)
    return sites


def test_the_old_rotation_is_called_from_exactly_one_place(tmp_path):
    """Без него молча проехало бы: ДВЕ ротации на одну вещь.

    Так уже было: `/backup_now` в `tools/jarvis_smart_telegram_control.py`
    звал `rotate_old_backups()` напрямую, а его докстрока утверждала «same
    critical-file allowlist + rotation the daily task runs». Два числа на одну
    вещь, и меньшее гасит большее молча: годовой клиентский набор попадает под
    порог в 14 суток и удаляется по кнопке в телеграме, а сообщение при этом
    рапортует об успешной ротации.

    СТРОГИЙ ВАРИАНТ: единственная разрешённая точка вызова — тело
    `rotate_all_backups` в самом `app/services/state_backup.py`. Всё
    остальное (`app/`, `scripts/`, `tools/`) обязано звать обёртку, которая
    знает про ОБА префикса и их разные сроки.
    """
    stray = [
        (rel, line, fn)
        for rel, line, fn in _rotation_call_sites(ROOT)
        if not (rel == ROTATION_HOME and fn == ROTATION_WRAPPER)
    ]
    assert not stray, (
        f"`{ROTATION_CALLEE}(` зовут мимо `{ROTATION_WRAPPER}`:\n"
        + "\n".join(
            f"  {rel}:{line} — внутри {fn or '<модуль целиком>'}" for rel, line, fn in stray
        )
        + f"\nЭта ротация знает ОДИН префикс и ОДИН порог. Клиентский набор "
        f"хранится год, а `state` — 14 суток; вызов мимо обёртки применит "
        f"меньший срок ко всему и удалит годовой набор молча.\n"
        f"Разрешённая точка ровно одна: тело `{ROTATION_WRAPPER}` в "
        f"`{ROTATION_HOME}`."
    )


def test_the_rotation_scanner_sees_real_calls_and_ignores_comments(tmp_path):
    """Без него молча проехало бы: сторож выше зелен, потому что ничего не видит.

    Статический сторож, разбирающий дерево, обязан сам быть проверен на
    подложенном примере: зелёный «нарушений нет» и зелёный «я не нашёл ни
    одного файла» выглядят одинаково. Здесь сканеру подсовывается дерево, где
    есть все четыре случая сразу — живой вызов, вызов на уровне модуля,
    разрешённая точка внутри обёртки и упоминания в комментарии/строке.
    """
    root = tmp_path / "fake_repo"
    (root / "tools").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "app" / "services").mkdir(parents=True)

    (root / "tools" / "control.py").write_text(
        "from app.services import state_backup as sb\n"
        "\n"
        "def _backup_now_dispatch(chat_id):\n"
        "    deleted = sb.rotate_old_backups()\n"
        "    return deleted\n",
        encoding="utf-8",
    )
    (root / "scripts" / "toplevel.py").write_text(
        "from app.services.state_backup import rotate_old_backups\n"
        "\n"
        "rotate_old_backups()\n",
        encoding="utf-8",
    )
    (root / "app" / "services" / "state_backup.py").write_text(
        '"""Модуль ротации. Слово rotate_old_backups в докстроке — не вызов."""\n'
        "\n"
        "def rotate_old_backups(**kw):\n"
        "    return []\n"
        "\n"
        "def rotate_all_backups(**kw):\n"
        "    return {'state': rotate_old_backups(**kw)}\n",
        encoding="utf-8",
    )
    (root / "scripts" / "only_mentions.py").write_text(
        "# было: deleted = sb.rotate_old_backups()\n"
        "NAME = 'rotate_old_backups'\n"
        "\n"
        "def helper():\n"
        '    """Раньше здесь звали rotate_old_backups()."""\n'
        "    return NAME\n",
        encoding="utf-8",
    )

    found = {(rel, fn) for rel, _line, fn in _rotation_call_sites(root)}
    assert ("tools/control.py", "_backup_now_dispatch") in found, (
        "сканер НЕ увидел живой вызов в `tools/control.py` — значит зелёный "
        f"сторож выше ничего не доказывает. Найдено: {sorted(found)}"
    )
    assert ("scripts/toplevel.py", None) in found, (
        "сканер НЕ увидел вызов на уровне модуля (вне функции) — обход не "
        f"доходит до тела модуля. Найдено: {sorted(found)}"
    )
    assert (ROTATION_HOME, ROTATION_WRAPPER) in found, (
        "сканер не нашёл РАЗРЕШЁННЫЙ вызов внутри обёртки; значит правило "
        "«кроме тела rotate_all_backups» проверить не на чем. "
        f"Найдено: {sorted(found)}"
    )
    assert not [rel for rel, _fn in found if rel == "scripts/only_mentions.py"], (
        "сканер посчитал вызовом комментарий, строку или упоминание в "
        "докстроке — это текстовый поиск, а не AST. Такой сторож красен "
        f"вечно и потому будет выключен. Найдено: {sorted(found)}"
    )


# ── гигиена самих реестров ───────────────────────────────────────────────────
def test_every_registry_entry_has_a_reason_that_names_the_damage():
    """Без него молча проехало бы: запись без причины через полгода не отличить
    от случайной строки, и следующий автор снимет её как лишнюю."""
    for rel, why in MUST_TRAVEL + MUST_NOT_TRAVEL:
        assert len(why) >= 40, (
            f"у `{rel}` причина слишком коротка, чтобы объяснить, чем "
            f"оборачивается ошибка: {why!r}"
        )


def test_the_registries_are_instances_not_a_copy_of_the_patterns():
    """Без него молча проехало бы: реестр, переписанный с `FORBIDDEN_PATTERNS`.

    Такой реестр согласен с ситом по определению и молчит ровно там, где сито
    забыло. Признак независимости: записи — конкретные экземпляры без `*`, и
    хотя бы часть из них ловится ТОЛЬКО через подстановку.
    """
    for rel, _why in MUST_TRAVEL + MUST_NOT_TRAVEL:
        assert "*" not in rel, (
            f"запись реестра `{rel}` — это ГЛОБ, а не экземпляр: реестр, "
            f"повторяющий шаблоны, проверяет шаблоны сами на себя."
        )
    only_by_wildcard = [
        rel for rel, _ in MUST_NOT_TRAVEL if rel not in PINNED_FORBIDDEN
    ]
    assert only_by_wildcard, (
        "каждая запись реестра запрещённого дословно равна какому-то шаблону "
        "— значит реестр переписан с сита и не проверяет подстановку вовсе."
    )
