# -*- coding: utf-8 -*-
"""Публичная дверь: ДВЕНАДЦАТЬ сторожей, писаных ОТ СПЕКИ, ДО КОДА.

Спека: `spec-b-public-door.md` (ВЕБ, волна 1 / пара B), §5 — двенадцать
пунктов, по одному на каждое решение §2.1–§2.9. Реализации на момент письма НЕ
СУЩЕСТВУЕТ: автор этих сторожей её не видел и не искал
([[jarvis-guards-not-by-the-plan-author]]).

🔴 СЕГОДНЯ ЭТОТ ФАЙЛ ПАДАЕТ НА СБОРЕ, И ЭТО НОРМА. Модуль
`app.routers.public_door` импортируется ОБЫЧНЫМ import на уровне файла — без
`try/except` и без `pytest.importorskip`. Сторож, который «пропускается», пока
кода нет, зелёный ПО ПОСТРОЕНИЮ, а зелёное по построению здесь считается
враньём — этот класс в доме уже ловили (см. запись про экран, съеденный при
записи файла). Красный сбор — честный отчёт «двери нет».

ПОЧЕМУ ЭТИ ДВЕНАДЦАТЬ, А НЕ ДРУГИЕ. Это первая по-настоящему ПУБЛИЧНАЯ точка
входа в систему (§1: сегодня входящей HTTP-двери нет ни одной). Цена ошибки
здесь не «чужой увидел панель», а «чужой ПИШЕТ в диалоги клиента» (§4).
Поэтому каждое из шести защитных решений — выключено по умолчанию, fail-closed
на полу-настройке, единый ответ на отказы, лимит, потолок тела, след — имеет
свой сторож, и все шесть ломаются ТИХО, то есть без сторожа их поломку никто
не заметит.

ЧТО ЗДЕСЬ МЕРЯЕТСЯ. ИСХОД: код ответа, байты тела, был ли позван `sink`, что
легло в след. НЕ текст реализации. Единственное исключение — сторож 6:
«сравнение постоянно по времени» это утверждение О КОДЕ, и наблюдаемого исхода
у него нет (утечка по времени в тесте не измеряется), поэтому он и только он
идёт по AST.

ЧЕГО ЗДЕСЬ НЕТ: сети, живого сокета, живого `state/`. Приложение поднимается
`TestClient`ом, след пишется в `tmp_path`. В рабочее дерево не пишется НИЧЕГО
— тесты этого репозитория и так меряют осадок в дереве (DEV-78), и добавлять
туда ещё один писатель нельзя.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 🔴 ОБЫЧНЫЙ импорт, намеренно. См. докстринг файла.
from app.routers import public_door
from app.routers.public_door import (PUBLIC_DOOR_PATH, build_public_door,
                                     install_public_door)

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"

# ── литералы контракта ──────────────────────────────────────────────────────
#
# Путь ручки приезжает КОНСТАНТОЙ из модуля (`PUBLIC_DOOR_PATH`), а не строкой
# в каждом месте: строка, повторённая в сторожах и в реализации, — это два
# места на одну вещь, и разъезжаются они молча.
#
# Имена заголовков и ключей конфига — ЛИТЕРАЛЫ здесь: они и есть предмет
# договора между этими сторожами и тем, кто будет писать дверь.

# Имя переменной окружения названо в спеке §2.1 вслух.
ENV_KEY = "JARVIS_PUBLIC_DOOR_KEY"

CLIENT_HEADER = "X-Door-Client"
TOKEN_HEADER = "X-Door-Token"

# Токены заведомо длиннее 32 (§2.2) и ПРИМЕТНЫЕ: их появление в следе или в
# конверте нельзя будет списать на совпадение символов.
TOKEN_ACME = "acme-token-QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ"
TOKEN_BETA = "beta-token-WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"
TOKEN_SLEEPY = "sleepy-token-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"

# Маркер тела: если он окажется в следе — тело запроса утекло в лог (§2.8).
BODY_MARKER = "SEKRET-BODY-MARKER-9f2c"
BODY = {"visitor": "v-1", "text": BODY_MARKER}

# Ключи строки следа (§2.8: время, клиент (или `?`), исход, причина).
TRAIL_KEYS = {"ts", "client", "outcome", "reason"}

# Заголовки, которые ставит транспорт, а не дверь: сверять их между ответами
# бессмысленно (см. сторож 5).
_TRANSPORT_HEADERS = {"date", "server"}

_spec = _ilu.spec_from_file_location("ops_watchdog_public_door", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)


# ── стенд ───────────────────────────────────────────────────────────────────

def _config(tmp_path, **over) -> dict:
    """Полностью настроенная дверь: три клиента, свой след в `tmp_path`.

    Лимиты по умолчанию заведомо БОЛЬШИЕ: сторож, который проверяет не лимит,
    не имеет права упереться в лимит и покраснеть чужой краснотой.

    `trail_path` ВСЕГДА в `tmp_path`. В `state/` рабочего дерева не пишется
    ничего и ни при каких условиях (DEV-78).
    """
    cfg = {
        "clients": {
            "acme": {"token": TOKEN_ACME, "enabled": True},
            "beta": {"token": TOKEN_BETA, "enabled": True},
            # Выключенный клиент — третий из трёх отказов §2.4.
            "sleepy": {"token": TOKEN_SLEEPY, "enabled": False},
        },
        "trail_path": str(tmp_path / "public_door.jsonl"),
        "max_body_bytes": 4096,
        "per_client_per_minute": 1000,
        "per_ip_per_minute": 1000,
    }
    cfg.update(over)
    return cfg


def _recording_sink(box, *, boom: bool = False):
    """Приёмник-шов (§2.7). `boom=True` — приёмник, который упал."""

    def sink(envelope):
        box.append(envelope)
        if boom:
            raise RuntimeError("приёмник недоступен: очередь не отвечает")

    return sink


def _door(tmp_path, monkeypatch, *, sink, key: str = "door-key",
          reraise: bool = True, **over):
    """Собранное приложение с примонтированной дверью. Возвращает `TestClient`.

    `reraise=False` там, где приёмник падает НАМЕРЕННО: с умолчанием
    `TestClient` вытаскивает необработанное исключение наружу, и сторож упал бы
    трейсбеком вместо внятного «ждали 503, получили 500». Ответ при этом не
    подменяется: непойманное падение всё равно даёт 500, то есть красное.
    """
    monkeypatch.setenv(ENV_KEY, key)
    app = FastAPI()
    install_public_door(app, config=_config(tmp_path, **over), sink=sink)
    return TestClient(app, raise_server_exceptions=reraise)


def _post(client, *, name="acme", token=TOKEN_ACME, body=None, content=None):
    """Один стук в дверь. `content` — сырые байты в обход json-сериализации."""
    headers = {CLIENT_HEADER: name, TOKEN_HEADER: token}
    if content is not None:
        headers["Content-Type"] = "application/json"
        return client.post(PUBLIC_DOOR_PATH, content=content, headers=headers)
    return client.post(PUBLIC_DOOR_PATH,
                       json=BODY if body is None else body, headers=headers)


def _mounted_paths(app) -> set:
    return {getattr(r, "path", None) for r in app.routes}


def _trail_lines(cfg_path) -> list:
    """Строки следа как (сырой текст, разобранный словарь)."""
    path = Path(cfg_path)
    assert path.exists(), (
        "следа %s НЕТ ни одной строки — дверь наружу без следа это ровно та "
        "тишина, из-за которой 26.08 сутки никто не знал об обрыве (§2.8)"
        % path)
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            out.append((raw, json.loads(raw)))
    return out


# ── 1 ───────────────────────────────────────────────────────────────────────

def test_door_without_key_is_not_mounted_at_all(tmp_path, monkeypatch):
    """Ключа нет — МАРШРУТА НЕТ (§2.1, §5.1).

    Требование сформулировано именно так: «не 404 у смонтированного, а
    ОТСУТСТВИЕ маршрута». Разница не косметическая. Смонтированный роут, за
    которым стоит проверка ключа, — это код, исполняющийся на каждом чужом
    запросе: ошибка в этой проверке открывает дверь. Несмонтированного роута
    открыть нельзя ничем, включая опечатку. Ровно так живут панели
    (`panels_enabled()` — «вимкнено за замовчуванням»), и мерж этой арки не
    имеет права открыть наружу хоть что-нибудь сам по себе.
    """
    assert public_door.ENV_KEY == ENV_KEY, (
        "имя переменной окружения двери: ждали %r (§2.1), в модуле %r — "
        "переменная, названная в спеке и в коде по-разному, означает дверь, "
        "которую владелец включает не тем тумблером"
        % (ENV_KEY, public_door.ENV_KEY))

    monkeypatch.delenv(ENV_KEY, raising=False)
    app = FastAPI()
    install_public_door(app, config=_config(tmp_path), sink=lambda env: None)

    assert PUBLIC_DOOR_PATH not in _mounted_paths(app), (
        "без %s маршрут %s ВСЁ РАВНО смонтирован — приложение поднялось с "
        "публичной точкой входа, которую владелец не включал; открыть её "
        "теперь может любая ошибка в проверке ключа"
        % (ENV_KEY, PUBLIC_DOOR_PATH))

    # Второе наблюдение того же факта, с другой стороны: снаружи двери нет.
    resp = _post(TestClient(app))
    assert resp.status_code == 404, (
        "стук в невыключенную дверь дал %s вместо 404: маршрута в списке нет, "
        "а отвечает кто-то — значит дверь примонтирована мимо `app.routes`"
        % resp.status_code)

    # И симметрия: с ключом дверь появляется. Без этой половины сторож был бы
    # зелен и на реализации, которая не монтирует дверь НИКОГДА.
    monkeypatch.setenv(ENV_KEY, "door-key")
    live = FastAPI()
    install_public_door(live, config=_config(tmp_path), sink=lambda env: None)
    assert PUBLIC_DOOR_PATH in _mounted_paths(live), (
        "ключ %s задан, а маршрута %s нет: дверь не включается вовсе, и все "
        "остальные сторожа мерили бы пустоту" % (ENV_KEY, PUBLIC_DOOR_PATH))


# ── 2 ───────────────────────────────────────────────────────────────────────

def test_door_with_key_but_no_clients_refuses_to_build(tmp_path):
    """Ключ есть, клиентов нет — сборка ОТКАЗЫВАЕТ и называет причину (§2.2).

    Полу-настроенная дверь опаснее выключенной: она отвечает. Дверь без единого
    клиента не может принять НИ ОДНОГО законного запроса — то есть она либо
    молча отвергает всё (и владелец сутки ищет, почему виджет молчит), либо,
    что хуже, реализована «как-нибудь» и пускает всех. Оба исхода тихие,
    поэтому отказ обязан случиться НА СБОРКЕ, где его увидит тот, кто запускал.

    Причина обязана быть НАЗВАНА словами: «дверь не поднялась» без объяснения
    — это тот же тупик, из которого владелец не выйдет без чтения кода.
    """
    cfg = _config(tmp_path, clients={})
    with pytest.raises((RuntimeError, ValueError)) as exc:
        build_public_door(cfg, sink=lambda env: None)

    msg = str(exc.value).lower()
    assert "клиент" in msg or "client" in msg, (
        "сборка отказала, но причину не назвала: %r. Владелец обязан прочесть "
        "«клиентов нет», а не догадываться — иначе он пойдёт чинить туннель"
        % str(exc.value))


# ── 3 ───────────────────────────────────────────────────────────────────────

def test_client_without_token_or_with_short_token_refuses_to_build(tmp_path):
    """Клиент без токена / токен короче 32 — отказ сборки с причиной (§2.2).

    Два разных способа объявить клиента «наполовину», и оба дают дверь, которая
    отвечает. Клиент без токена — это либо клиент, которого не пустят никогда,
    либо (при неаккуратной реализации) клиент, которого пустят С ЛЮБЫМ токеном,
    включая пустой. Короткий токен — это дверь, которую подбирают перебором:
    §2.5 ставит лимит на скорость, но лимит не спасает от токена, который
    угадывается с сотни попыток.

    В обоих случаях отказ обязан НАЗВАТЬ КЛИЕНТА: конфиг с десятком клиентов
    без имени виноватого чинится вслепую.
    """
    for label, clients, culprit in (
        ("клиент объявлен без токена",
         {"silent": {"enabled": True}}, "silent"),
        ("токен короче 32 символов",
         {"shorty": {"token": "s" * 31, "enabled": True}}, "shorty"),
    ):
        cfg = _config(tmp_path, clients=clients)
        with pytest.raises((RuntimeError, ValueError)) as exc:
            build_public_door(cfg, sink=lambda env: None)

        text = str(exc.value)
        low = text.lower()
        assert culprit in low, (
            "%s: сборка отказала, но клиента %r не назвала: %r — конфиг с "
            "десятком клиентов чинится вслепую" % (label, culprit, text))
        assert "токен" in low or "token" in low, (
            "%s: в причине отказа нет слова про токен: %r — владелец не "
            "поймёт, ЧЕГО не хватает" % (label, text))


# ── 4 ───────────────────────────────────────────────────────────────────────

def test_valid_token_yields_202_and_calls_sink_exactly_once(tmp_path,
                                                            monkeypatch):
    """Верный токен → `202`, `sink` позван РОВНО один раз, конверт целый (§2.7).

    Это единственный сторож про то, что дверь вообще РАБОТАЕТ, и он же ловит
    два тихих дефекта приёмника-шва:

    * `sink` не позван вовсе, а `202` отдан — сообщение посетителя исчезло, и
      узнают об этом от клиента, а не от системы;
    * `sink` позван ДВАЖДЫ (повтор при ретрае внутри ручки) — посетитель
      увидит два одинаковых ответа бота.

    Конверт проверяется целиком: дверь не имеет права ни потерять тело, ни
    забыть, ЧЕЙ это запрос. И токена в конверте быть не должно — конверт едет
    дальше по системе и осядет в очереди волны 2.
    """
    box = []
    client = _door(tmp_path, monkeypatch, sink=_recording_sink(box))
    resp = _post(client)

    assert resp.status_code == 202, (
        "верный токен дал %s вместо 202 — дверь не принимает законный "
        "запрос, весь остальной путь мёртв" % resp.status_code)
    assert len(box) == 1, (
        "`sink` позван %d раз(а) вместо ровно одного: ноль — сообщение "
        "посетителя исчезло при ответе 202; больше одного — лид получит "
        "дубль" % len(box))

    env = box[0]
    assert isinstance(env, dict), (
        "в `sink` уехал %s, а не словарь-конверт — приёмник волны 2 такого не "
        "разберёт" % type(env).__name__)
    assert env.get("client") == "acme", (
        "в конверте клиент %r вместо 'acme' — приёмник не узнает, в ЧЬИ "
        "диалоги класть сообщение" % env.get("client"))
    assert env.get("payload") == BODY, (
        "тело в конверте %r не равно отправленному %r — дверь потеряла или "
        "переписала сообщение посетителя" % (env.get("payload"), BODY))
    dumped = json.dumps(env, ensure_ascii=False, default=str)
    assert TOKEN_ACME not in dumped, (
        "токен клиента уехал в конверте дальше по системе — он осядет в "
        "очереди и в её бэкапах, а это секрет, открывающий дверь")


# ── 5 ───────────────────────────────────────────────────────────────────────

def test_three_kinds_of_refusal_are_byte_identical(tmp_path, monkeypatch):
    """Неверный токен, неизвестный клиент, выключенный клиент — ОДИН ответ (§2.4).

    Побайтово. Не «все три дали 401», а «все три дали ОДНО И ТО ЖЕ».

    Различимые отказы превращают дверь в оракул: тот, кто перебирает, по
    разнице ответов узнаёт СНАЧАЛА список живых клиентов (неизвестный клиент
    отвечает не так, как известный), а потом уже подбирает токен только к ним.
    Разница может быть в одном байте тела, в лишнем заголовке, в длине —
    поэтому сверяются байты, а не смысл.

    Заголовки транспорта (`date`, `server`) из сверки исключены: их ставит не
    дверь, и `date` отличается уже потому, что запросы шли не в одну секунду.
    """
    client = _door(tmp_path, monkeypatch, sink=_recording_sink([]))

    answers = {
        "неверный токен у известного клиента":
            _post(client, name="acme", token="x" * 40),
        "неизвестный клиент":
            _post(client, name="nosuch", token=TOKEN_ACME),
        "выключенный клиент с ВЕРНЫМ токеном":
            _post(client, name="sleepy", token=TOKEN_SLEEPY),
    }

    for label, resp in answers.items():
        assert resp.status_code == 401, (
            "«%s» дал %s вместо 401 — отказ отличим по коду, и перебор "
            "начинается с этого" % (label, resp.status_code))

    labels = list(answers)
    base_label = labels[0]
    base = answers[base_label]
    base_headers = sorted((k.lower(), v) for k, v in base.headers.items()
                          if k.lower() not in _TRANSPORT_HEADERS)

    for label in labels[1:]:
        other = answers[label]
        assert other.content == base.content, (
            "тела отказов РАЗНЫЕ: «%s» -> %r, «%s» -> %r. Дверь рассказывает, "
            "что именно не сошлось, — это оракул: сначала выясняют список "
            "клиентов, потом подбирают токен только к живым"
            % (base_label, base.content, label, other.content))
        other_headers = sorted((k.lower(), v) for k, v in other.headers.items()
                               if k.lower() not in _TRANSPORT_HEADERS)
        assert other_headers == base_headers, (
            "заголовки отказов РАЗНЫЕ: «%s» -> %s, «%s» -> %s. Тело совпало, а "
            "отличие уехало в заголовок — тот же оракул, только незаметнее"
            % (base_label, base_headers, label, other_headers))


# ── 6 ───────────────────────────────────────────────────────────────────────

def test_token_is_compared_in_constant_time_not_by_equals():
    """Сравнение токена — `compare_digest`, а не `==` (§2.3). ПИН ПО AST.

    Единственный сторож в файле, который смотрит на КОД, а не на исход, и это
    не лень: утечка по времени наблюдаемого исхода не имеет вовсе. Ответ на
    `==` и на `compare_digest` побайтово одинаков — разница только в том,
    сколько наносекунд ушло на отказ, и по этой разнице токен восстанавливается
    посимвольно. Измерить это в тесте нельзя (шум планировщика больше
    сигнала), доказать в коде — можно.

    Ищем ДВА факта:
      * `compare_digest` зовётся (спека называет `hmac.compare_digest`;
        `secrets.compare_digest` — та же функция, поэтому принимается тоже:
        сторож пинит постоянное по времени сравнение, а не имя модуля);
      * НИ ОДНОГО `==`/`!=`, у которого хоть один операнд упоминает токен.
    """
    source = Path(public_door.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "compare_digest"]
    assert calls, (
        "в %s нет ни одного вызова `compare_digest` — значит токен сравнивают "
        "как-то иначе, и отказ отдаёт время сравнения; по нему токен "
        "восстанавливается посимвольно" % Path(public_door.__file__).name)

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left] + list(node.comparators)
        for operand in operands:
            piece = ast.get_source_segment(source, operand) or ""
            if "token" in piece.lower():
                bad.append((getattr(node, "lineno", "?"),
                            ast.get_source_segment(source, node)))
                break

    assert not bad, (
        "токен сравнивается через `==`/`!=`: %s. Строковое сравнение выходит "
        "на первом несовпавшем символе — это и есть утечка по времени, ради "
        "которой §2.3 требует `compare_digest`" % bad)


# ── 7 ───────────────────────────────────────────────────────────────────────

def test_per_client_flood_gets_429_with_retry_after(tmp_path, monkeypatch):
    """Потолок на клиента → `429` и `Retry-After` (§2.5).

    Без лимита первый же цикл без бэкоффа на той стороне съедает процесс:
    27.08 такой цикл дал 43418 трейсбеков и 100 МБ лога за сутки
    ([[jarvis-retry-loops-without-backoff]]). Дверь наружу обязана уметь
    сказать «позже», а не только «нет».

    `Retry-After` — не украшение. Это ЕДИНСТВЕННОЕ, что превращает «нет» в
    бэкофф на той стороне: без него корректный клиент повторит немедленно, и
    429 станет таким же циклом, только с другим кодом.
    """
    client = _door(tmp_path, monkeypatch, sink=_recording_sink([]),
                   per_client_per_minute=2, per_ip_per_minute=1000)

    codes = [_post(client).status_code for _ in range(3)]
    assert codes[:2] == [202, 202], (
        "первые два запроса в пределах потолка дали %s вместо [202, 202] — "
        "лимит режет законный трафик" % codes[:2])
    assert codes[2] == 429, (
        "третий запрос при потолке 2/мин дал %s вместо 429 — лимита на "
        "клиента нет, и цикл без бэкоффа на той стороне съест процесс"
        % codes[2])

    last = _post(client)
    retry = last.headers.get("retry-after")
    assert retry is not None, (
        "в 429 нет заголовка Retry-After — корректный клиент повторит "
        "немедленно, и отказ станет тем же циклом без бэкоффа")
    assert retry.strip().isdigit() and int(retry) > 0, (
        "Retry-After = %r: не целое число секунд больше нуля, то есть «повтори "
        "прямо сейчас» — бэкоффа опять нет" % retry)


# ── 8 ───────────────────────────────────────────────────────────────────────

def test_ip_flood_is_capped_even_when_the_client_changes(tmp_path, monkeypatch):
    """Потолок на АДРЕС держится при смене клиента (§2.5, §5.8).

    Лимит только на клиента обходится тривиально: у того, кто ломится, есть
    список клиентов (или он его переберёт), и он раскладывает поток по именам.
    Потолок на адрес — второй счётчик, и он обязан считать ВЕСЬ поток с адреса,
    а не поток на пару «адрес+клиент», иначе это тот же лимит на клиента под
    другим именем.

    Стенд: потолок на клиента заведомо большой (1000), на адрес маленький (3).
    Три запроса от `acme` исчерпывают адрес; четвёртый идёт от `beta`, который
    НЕ отправил ещё ничего, — и обязан получить 429.
    """
    client = _door(tmp_path, monkeypatch, sink=_recording_sink([]),
                   per_client_per_minute=1000, per_ip_per_minute=3)

    warmup = [_post(client, name="acme", token=TOKEN_ACME).status_code
              for _ in range(3)]
    assert warmup == [202, 202, 202], (
        "три запроса при потолке адреса 3/мин дали %s вместо трёх 202 — "
        "стенд не набрал потолок, и дальнейшее измерение бессмысленно"
        % warmup)

    other = _post(client, name="beta", token=TOKEN_BETA)
    assert other.status_code == 429, (
        "запрос от ДРУГОГО клиента (свой счётчик пуст) с того же адреса дал "
        "%s вместо 429 — потолок на адрес считает пару «адрес+клиент», то "
        "есть обходится сменой имени в заголовке" % other.status_code)


# ── 9 ───────────────────────────────────────────────────────────────────────

def test_oversized_body_is_refused_before_the_json_parser_runs(tmp_path,
                                                               monkeypatch):
    """Тело больше потолка → `413`, и парсер JSON НЕ звался (§2.6).

    Порядок здесь — вся суть решения. «Разобрали, потом посмотрели размер» —
    это способ уронить процесс ОДНИМ запросом: разбор неограниченного тела
    съедает память до отказа машины (в этом доме исчерпание памяти дважды
    кончалось BSOD, см. запись про `0xEF`/`0x116`).

    ДОКАЗАТЕЛЬСТВО, А НЕ ВЕРА. Тело подсовывается ЗАВЕДОМО НЕРАЗБИРАЕМОЕ:
    `json.loads` на нём бросает. Если дверь ответит 400/422 — значит парсер до
    него добрался, то есть проверка размера стоит ПОСЛЕ разбора, и потолок не
    защищает ни от чего. Ответ 413 возможен только при проверке ДО.
    """
    client = _door(tmp_path, monkeypatch, sink=_recording_sink([]),
                   max_body_bytes=1024)

    # Сломанный JSON нужного объёма: закрывающих скобок нет вовсе.
    garbage = ('{"' + BODY_MARKER + '": [' + "1," * 5000).encode("utf-8")
    assert len(garbage) > 1024, "стенд не набрал объём выше потолка"
    with pytest.raises(ValueError):
        json.loads(garbage.decode("utf-8"))

    resp = _post(client, content=garbage)
    assert resp.status_code == 413, (
        "тело %d байт при потолке 1024 дало %s вместо 413. 400/422 означает, "
        "что до размера дело дошло ПОСЛЕ парсера — то есть неограниченное "
        "тело сначала разбирают, а потом отказывают; ровно этим процесс "
        "роняют одним запросом" % (len(garbage), resp.status_code))


# ── 10 ──────────────────────────────────────────────────────────────────────

def test_failed_sink_answers_503_and_is_not_written_down_as_accepted(
        tmp_path, monkeypatch):
    """`sink` бросил → `503`, а НЕ `202`; приём не записан успешным (§2.7).

    Приёмник — шов, и в волне 2 за ним будет очередь на диске. Очередь
    недоступна = сообщение посетителя НЕ ПРИНЯТО, и врать об этом нельзя:
    `202` на упавшем приёмнике означает, что посетитель ушёл довольным, клиент
    не получил ничего, а в системе не осталось даже следа о потере.

    Вторая половина — след. Строка «принято» на потерянном сообщении хуже
    отсутствия строки: по ней потом будут доказывать, что сообщение дошло.
    """
    cfg_trail = tmp_path / "public_door.jsonl"
    box = []
    client = _door(tmp_path, monkeypatch, reraise=False,
                   sink=_recording_sink(box, boom=True))

    resp = _post(client)
    assert resp.status_code == 503, (
        "приёмник упал, а дверь ответила %s. 202 здесь — прямая ложь "
        "посетителю: сообщение не принято никем и не сохранено нигде"
        % resp.status_code)
    assert len(box) == 1, (
        "`sink` позван %d раз(а): дверь либо не пыталась отдать сообщение, "
        "либо повторила отдачу внутри запроса" % len(box))

    outcomes = [row["outcome"] for _, row in _trail_lines(cfg_trail)]
    assert 202 not in outcomes, (
        "в следе есть строка с исходом 202 при упавшем приёмнике: %s. По ней "
        "потом будут доказывать, что сообщение дошло" % outcomes)
    assert 503 in outcomes, (
        "отказ приёмника не оставил строки 503 в следе: %s — потеря "
        "сообщения прошла бесследно" % outcomes)


# ── 11 ──────────────────────────────────────────────────────────────────────

def test_every_outcome_leaves_a_trail_line_without_token_or_body(
        tmp_path, monkeypatch):
    """Все пять исходов в следе; токена и тела в нём НЕТ (§2.8).

    Две беды в одном сторожe, потому что они противоположные и чинятся друг
    против друга.

    ПЕРВАЯ: исход без следа. Дверь наружу, о которой ничего не остаётся на
    диске, — это ровно та тишина, из-за которой 26.08 сутки никто не знал об
    обрыве. Отказы (401/429/413) важнее приёмов: по ним видно, что дверь
    ломают, и без них подбор токена проходит незамеченным.

    ВТОРАЯ: след, в который утекло лишнее. Соблазн «писать всё, чтобы потом
    разобраться» кладёт в файл ТОКЕН (то есть ключ от двери — в лог, в его
    ротацию и в бэкапы) и ТЕЛО ЗАПРОСА (переписку живых людей). Лог живёт
    дольше и копируется шире, чем что угодно другое, поэтому обе утечки
    пинятся здесь по СЫРОМУ тексту строки.
    """
    trail = tmp_path / "public_door.jsonl"

    # Умолчание пути пиним ЛИТЕРАЛОМ, но не пишем в него НИКОГДА: в этом
    # дереве тесты и так оставляют осадок в `state/` (DEV-78).
    default_trail = getattr(public_door, "DEFAULT_TRAIL_PATH", None)
    assert default_trail is not None, (
        "у двери нет модульной константы `DEFAULT_TRAIL_PATH` — путь следа "
        "живёт строкой в коде, и второе такое место разъедется молча")
    assert str(default_trail).replace("\\", "/") == "state/public_door.jsonl", (
        "умолчание пути следа %r, а спека §2.8 называет "
        "'state/public_door.jsonl' — проба и владелец будут смотреть в разные "
        "файлы" % str(default_trail))

    # 202 и 401 — на одном стенде; след у всех стендов ОБЩИЙ (один `trail_path`).
    ok_client = _door(tmp_path, monkeypatch, sink=_recording_sink([]))
    assert _post(ok_client).status_code == 202
    assert _post(ok_client, token="x" * 40).status_code == 401

    # 413 — свой стенд с низким потолком тела.
    small = _door(tmp_path, monkeypatch, sink=_recording_sink([]),
                  max_body_bytes=64)
    garbage = ('{"' + BODY_MARKER + '": [' + "1," * 200).encode("utf-8")
    assert _post(small, content=garbage).status_code == 413

    # 429 — свой стенд с потолком в один запрос.
    tight = _door(tmp_path, monkeypatch, sink=_recording_sink([]),
                  per_client_per_minute=1)
    _post(tight)
    assert _post(tight).status_code == 429

    # 503 — свой стенд с падающим приёмником.
    broken = _door(tmp_path, monkeypatch, reraise=False,
                   sink=_recording_sink([], boom=True))
    assert _post(broken).status_code == 503

    lines = _trail_lines(trail)
    outcomes = {row["outcome"] for _, row in lines}
    assert {202, 401, 413, 429, 503} <= outcomes, (
        "в следе нет строк для исходов %s (есть только %s) — эти запросы "
        "прошли через публичную дверь бесследно"
        % (sorted({202, 401, 413, 429, 503} - outcomes), sorted(outcomes)))

    for raw, row in lines:
        missing = TRAIL_KEYS - set(row)
        assert not missing, (
            "в строке следа нет полей %s: %r. Без времени, клиента, исхода и "
            "причины строка не отвечает ни на один вопрос, ради которого "
            "заводился след" % (sorted(missing), row))
        for secret, what in ((TOKEN_ACME, "ТОКЕН клиента"),
                             (BODY_MARKER, "ТЕЛО запроса")):
            assert secret not in raw, (
                "в след утёк %s: %r. Лог живёт дольше и копируется шире, чем "
                "что угодно другое — это ключ от двери (или переписка живых "
                "людей) в файле, который уедет в бэкап" % (what, raw))

    unknown = [row for _, row in lines if row["outcome"] == 401]
    assert any(row["client"] for row in unknown), (
        "у строк отказа поле `client` пустое — §2.8 требует имя клиента или "
        "`?`, а пустая строка не отличает «не назвался» от «поле не пишут»")


# ── 12 ──────────────────────────────────────────────────────────────────────

def test_probe_says_off_when_disabled_and_red_when_the_door_lets_anyone_in():
    """Проба `public_door`: `off` ≠ красное, распахнутая дверь = красное (§2.9).

    Три состояния, и путают их дорого.

    ВЫКЛЮЧЕННАЯ ДВЕРЬ — НОРМА, а не поломка. Дверь выключена по умолчанию
    (§2.1), и красная лампа на штатном состоянии — это вечно красная лампа. За
    неделю она становится фоном, и тогда гаснет ВСЁ, что рядом; у volska уже
    две таких ([[jarvis-volska-second-permanent-red-lamp]]).

    ЗЕЛЁНОЕ МЕРЯЕТСЯ ОТКАЗОМ ЧУЖОМУ, а не ответом. Проба «дверь жива» без
    вопроса «дверь отказывает без токена» — это лампа, зелёная у РАСПАХНУТОЙ
    двери, то есть худший из возможных отказов
    ([[jarvis-loud-failure-next-to-a-soothing-lamp]]). Поэтому измерение одно и
    то же: стучимся БЕЗ токена и смотрим, что ответили. 401 — дверь и жива, и
    закрыта. 202 — катастрофа: кто угодно пишет в диалоги клиента.

    Форма снимка — как у остальных снимочных проб (`probe_panel_client`,
    `probe_outgoing`): ОДИН позиционный словарь. Россыпь именованных аргументов
    уже стоила вердикта `no_bind_address` на здоровой панели.
    """
    off = ow.probe_public_door({"enabled": False})
    assert off.get("ok") is True, (
        "выключенная дверь даёт КРАСНОЕ (%r) — это штатное состояние (§2.1), "
        "и вечно красная лампа за неделю становится фоном вместе со всем, что "
        "рядом" % off)
    assert off.get("reason") == "off", (
        "выключенная дверь названа %r вместо 'off' — владелец не отличит "
        "«двери нет по решению» от «дверь молчит»" % off.get("reason"))

    green = ow.probe_public_door({"enabled": True, "host": "127.0.0.1",
                                  "port": 8080, "no_token_status": 401})
    assert green.get("ok") is True, (
        "живая дверь, отказавшая запросу БЕЗ токена, даёт красное (%r) — "
        "лампа краснеет на здоровой двери и её перестают читать" % green)
    assert green.get("reason") != "off", (
        "работающая дверь отчиталась как 'off' — включённую дверь никто не "
        "меряет вовсе")

    silent = ow.probe_public_door({"enabled": True, "host": "127.0.0.1",
                                   "port": 8080, "no_token_status": None})
    assert silent.get("ok") is False, (
        "включённая дверь не отвечает, а проба зелёная (%r) — «не смогли "
        "спросить» обязано быть красным" % silent)

    wide_open = ow.probe_public_door({"enabled": True, "host": "127.0.0.1",
                                      "port": 8080, "no_token_status": 202})
    assert wide_open.get("ok") is False, (
        "дверь ПРИНЯЛА запрос без токена, а проба зелёная (%r). Это не «лампа "
        "неточная», это открытая наружу запись в диалоги клиентов при полном "
        "молчании сторожей" % wide_open)
    assert wide_open.get("reason") not in (None, "", silent.get("reason")), (
        "распахнутая дверь и молчащая дверь идут под одной причиной %r — "
        "владелец пойдёт поднимать процесс там, где надо закрывать дверь"
        % wide_open.get("reason"))
