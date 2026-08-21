# -*- coding: utf-8 -*-
"""DEV-46 §3.3: конверт клиентского набора. Сторожа ОТ СПЕКИ, до реализации.

Владелец выбрал **вариант B** (§3.3, ответ 1): на хосте лежит только
ПУБЛИЧНЫЙ ключ, хост умеет ПИСАТЬ бэкап и не умеет его ЧИТАТЬ. Дословно:
«Хост арендованный, „заберут вместе с диском“ — не гипотеза». Всё, что
проверяется ниже, существует ради одного: чтобы это утверждение осталось
правдой не в спеке, а в байтах.

ЧТО ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ И ПОЧЕМУ. Этот файл сторожит ПРИМИТИВ
(`app/services/backup_crypto.py`), а не конвейер бэкапа. Ключ объекта,
состав набора, ротация по префиксам, улика дрила — §7 пп. 1–7, 10, 11 —
живут в других файлах и у других авторов. Здесь ровно две вещи из §7:
п. 8 (псевдоним стабилен и различает) и п. 9 в его модульной половине
(хост не умеет прочитать то, что записал).

ПОЧЕМУ ГЛАВНЫЙ ТЕСТ — СТРУКТУРНЫЙ, А НЕ ПОВЕДЕНЧЕСКИЙ. Поведенческий сторож
на «расшифровка провалилась» зеленеет ровно до того дня, когда кто-то добавит
`load_private_key()` «временно, чтобы дрил гонялся на хосте» (§9.2 запрещает
это дословно). Поведение при этом не меняется НИ В ОДНОМ существующем тесте:
дешифровка как падала без ключа, так и падает — просто теперь ключ можно
достать. Поэтому запрет держится разбором AST модуля: он краснеет на
ПОЯВЛЕНИИ такого кода, а не на его отсутствии.

ЖИВЫХ КЛЮЧЕЙ И СЕТИ ЗДЕСЬ НЕТ. Все ключи и соли порождаются в тестах.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "app" / "services" / "backup_crypto.py"

# Имена переменных окружения — ЛИТЕРАЛЬНО из контракта. Не импортировать из
# модуля: список, выведенный из реализации, согласен с ней по определению
# ([[jarvis-literal-lists-not-introspection]]).
PUBLIC_KEY_ENV = "JARVIS_BACKUP_PUBLIC_KEY"
SALT_ENV = "JARVIS_BACKUP_KEY_SALT"

KEY_LEN = 32
NONCE_LEN = 12
GCM_TAG_LEN = 16
HEX = set("0123456789abcdef")


# ── доступ к модулю ──────────────────────────────────────────────────────────
@pytest.fixture
def bc():
    """Модуля ещё нет — и это КРАСНОЕ, а не пропуск.

    `importorskip` здесь был бы прямым враньём: пропущенный сторож выглядит
    в отчёте так же, как сторож, которому нечего сказать.
    """
    try:
        from app.services import backup_crypto
    except Exception as exc:  # noqa: BLE001 — любая причина отказа одинаково красная
        pytest.fail(
            f"`app.services.backup_crypto` не импортируется: "
            f"{type(exc).__name__}: {exc}\n"
            f"Ожидался файл {MODULE_PATH}.",
            pytrace=False,
        )
    return backup_crypto


def _expect_module_error(bc, what: str, fn, *args, **kwargs):
    """Отказ обязан выйти наружу КЛАССОМ МОДУЛЯ, а не изнутри `cryptography`.

    Разница не косметическая: вызывающий код ловит `BackupCryptoError` и
    решает, что делать с бэкапом. `InvalidTag`/`binascii.Error`, пролетевшие
    мимо, попадут в общий `except Exception` конвейера и станут «бэкап
    пропущен, причина неизвестна» — то есть тихой потерей набора.
    """
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — тип проверяем ниже, он и есть предмет теста
        assert isinstance(exc, bc.BackupCryptoError), (
            f"{what}: наружу вышло `{type(exc).__module__}.{type(exc).__name__}: {exc}`, "
            f"а обязан `BackupCryptoError` модуля. Необёрнутое исключение из недр "
            f"`cryptography` вызывающий код не отличит от падения самого бэкапа."
        )
        return exc
    pytest.fail(
        f"{what}: вызов не отказал, а ВЕРНУЛ {result!r} ({type(result).__name__}). "
        f"Молчаливое «продолжаем без шифрования» — ровно тот дефект, ради которого "
        f"выбран вариант B."
    )


def _header_len(bc) -> int:
    return len(bc.MAGIC) + 1 + KEY_LEN + NONCE_LEN


def _flip_bit(blob: bytes, index: int, bit: int = 0) -> bytes:
    raw = bytearray(blob)
    raw[index] ^= 1 << bit
    return bytes(raw)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ══════════════════════════════════════════════════════════════════════════════
# 0. Формат конверта пришпилен
# ══════════════════════════════════════════════════════════════════════════════
def test_the_envelope_constants_are_pinned_so_a_format_change_cannot_be_silent(bc):
    """Без пина смена формата обнаружится через год — на дриле восстановления."""
    assert bc.MAGIC == b"JRVCLI1\x00", (
        f"магический префикс изменился: {bc.MAGIC!r} вместо b'JRVCLI1\\x00'. "
        f"Прошлогодние объекты в бакете этим кодом уже не откроются, а узнаем мы "
        f"об этом на восстановлении, когда данные понадобятся."
    )
    assert len(bc.MAGIC) == 8, (
        f"длина MAGIC {len(bc.MAGIC)} вместо 8 — сдвигаются ВСЕ смещения заголовка "
        f"(версия, эфемерный ключ, nonce)."
    )
    assert bc.VERSION == 1, f"VERSION={bc.VERSION}; поднимать версию — осознанное действие"
    assert bc.MAX_PLAINTEXT_BYTES == 512 * 1024 * 1024, (
        f"MAX_PLAINTEXT_BYTES={bc.MAX_PLAINTEXT_BYTES} вместо 512 МиБ. Потолок — "
        f"предохранитель памяти хоста: база клиента растёт с каждым сообщением."
    )


def test_the_envelope_layout_matches_the_contract_byte_for_byte(bc):
    """Иначе разбор заголовка «works on my machine» и врёт на чужом объекте."""
    _priv, pub = bc.generate_keypair()
    data = b"payload" * 64
    blob = bc.encrypt_for(pub, data, aad=b"backups/client/2026-08-22/deadbeef/db")

    assert blob[: len(bc.MAGIC)] == bc.MAGIC, (
        f"конверт не начинается с MAGIC: {blob[:8]!r}. Без префикса чужой файл в "
        f"бакете неотличим от нашего объекта."
    )
    assert blob[len(bc.MAGIC)] == bc.VERSION, (
        f"байт версии = {blob[len(bc.MAGIC)]}, ожидался {bc.VERSION}"
    )
    expected = _header_len(bc) + len(data) + GCM_TAG_LEN
    assert len(blob) == expected, (
        f"длина конверта {len(blob)}, по контракту "
        f"MAGIC(8)|version(1)|ephemeral_pub(32)|nonce(12)|AESGCM = {expected}. "
        f"Расхождение означает, что в заголовке появилось или пропало поле, а "
        f"смещения ниже (и в чужом коде-читателе) считаются по контракту."
    )


def test_aad_is_mandatory_and_keyword_only_so_it_cannot_be_forgotten(bc):
    """Без обязательности `aad` объект молча теряет привязку к дате и клиенту."""
    for name in ("encrypt_for", "decrypt_with"):
        sig = inspect.signature(getattr(bc, name))
        assert "aad" in sig.parameters, f"у `{name}` нет параметра `aad`: {sig}"
        param = sig.parameters["aad"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"`{name}`: `aad` объявлен как {param.kind}, а обязан быть KEYWORD_ONLY — "
            f"позиционный aad однажды перепутают с данными."
        )
        assert param.default is inspect.Parameter.empty, (
            f"`{name}`: у `aad` есть умолчание {param.default!r}. Умолчание = тихий "
            f"конверт без привязки к месту: такой объект можно переставить на другую "
            f"дату и другому клиенту, и никто не заметит (§9.1)."
        )

    _priv, pub = bc.generate_keypair()
    with pytest.raises(TypeError):
        bc.encrypt_for(pub, "данные".encode("utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Круговорот
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "size",
    [0, 1, 15, 16, 17, 4096, 1024 * 1024],
    ids=["пусто", "1Б", "15Б", "16Б", "17Б", "4КиБ", "1МиБ"],
)
def test_encrypt_then_decrypt_returns_the_original_bytes(bc, size):
    """Без этого весь конверт — генератор мусора, который никто не откроет.

    Граничные размеры (0 и вокруг блока GCM) ловят ошибки разбора заголовка:
    срез на единицу мимо на 4 КиБ незаметен, на пустых данных — фатален.
    """
    priv, pub = bc.generate_keypair()
    data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    assert len(data) == size
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"

    blob = bc.encrypt_for(pub, data, aad=aad)
    back = bc.decrypt_with(priv, blob, aad=aad)

    assert back == data, (
        f"круговорот на {size} байтах вернул не то: получено {len(back)} байт, "
        f"первое расхождение — "
        f"{next((i for i, (a, b) in enumerate(zip(back, data)) if a != b), 'длина')}."
    )
    assert isinstance(back, (bytes, bytearray)), f"вернулось {type(back).__name__}, а не bytes"


def test_generate_keypair_returns_two_distinct_32_byte_keys(bc):
    """Перепутанные местами приватный и публичный ключ хост не заметит НИКОГДА."""
    priv, pub = bc.generate_keypair()
    assert len(priv) == KEY_LEN, f"приватный ключ {len(priv)} байт вместо {KEY_LEN}"
    assert len(pub) == KEY_LEN, f"публичный ключ {len(pub)} байт вместо {KEY_LEN}"
    assert priv != pub, "generate_keypair вернул один и тот же материал дважды"
    other_priv, other_pub = bc.generate_keypair()
    assert (priv, pub) != (other_priv, other_pub), (
        "две пары подряд одинаковы — генератор детерминирован, и ключ владельца "
        "воспроизводится кем угодно на любой машине"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Хост не умеет читать (§7 п. 9, §9.2) — СТРУКТУРНЫЙ сторож
# ══════════════════════════════════════════════════════════════════════════════
_PRIVATE_WORDS = ("private", "privkey", "priv_key", "secret_key", "seckey")
_FETCH_WORDS = ("load", "read", "get", "fetch", "open", "resolve", "import")
_FILE_READERS = ("open", "read_bytes", "read_text", "read")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{3,}")
_PATHISH_RE = re.compile(r"[\w./\\-]+\.\w{1,8}")


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Строковые константы уровня модуля — чтобы косвенность не спасала.

    `_PRIV_ENV = "..."` + `env.get(_PRIV_ENV)` обязан краснеть так же, как
    литерал прямо в вызове.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value.value
    return out


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Докстроки из общего разбора литералов ИСКЛЮЧАЮТСЯ намеренно.

    Модуль обязан вслух объяснять, почему приватного ключа на хосте нет, —
    и объяснение по-русски или по-английски не должно красить сторожа. Это
    та самая нужная слепота к прозе, ради которой берётся AST, а не grep.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _string_operands(call: ast.Call, consts: dict[str, str]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.Name) and node.id in consts:
            found.append(consts[node.id])
    return found


def _lookup_key(node: ast.AST, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def private_key_access_violations(source: str, filename: str = "<образец>") -> list[tuple[str, str]]:
    """Разбор AST: найти в модуле ЛЮБОЙ способ добыть приватный ключ.

    Возвращает список пар (правило, объяснение). Пустой список = запрет цел.
    Наличие `decrypt_with(private_key, ...)` нарушением НЕ является: функцию
    вызывает машина владельца, передавая ключ снаружи. Нарушение — код,
    который добывает ключ САМ: по имени функции, из окружения или с диска.
    """
    tree = ast.parse(source, filename=filename)
    consts = _module_constants(tree)
    docstrings = _docstring_ids(tree)
    bad: list[tuple[str, str]] = []

    # (1) имя функции: `load_private_key`, `_read_privkey`, `get_secret_key`…
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        low = node.name.lower()
        if any(w in low for w in _PRIVATE_WORDS) and any(w in low for w in _FETCH_WORDS):
            bad.append((
                "имя-функции",
                f"{filename}:{node.lineno}: функция `{node.name}` — это способ ДОБЫТЬ "
                f"приватный ключ на хосте. §9.2: приватный ключ на хосте не появляется "
                f"НИКОГДА, ни для дрила, ни «временно, на время восстановления».",
            ))

    # (2) чтение окружения по ключу, в имени которого есть PRIVATE
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call):
            func = node.func
            is_getenv = isinstance(func, ast.Name) and func.id == "getenv"
            is_attr_get = isinstance(func, ast.Attribute) and func.attr in ("get", "getenv")
            if (is_getenv or is_attr_get) and node.args:
                key = _lookup_key(node.args[0], consts)
        elif isinstance(node, ast.Subscript):
            key = _lookup_key(node.slice, consts)
        if key and "PRIVATE" in key.upper():
            bad.append((
                "env-ключ",
                f"{filename}:{getattr(node, 'lineno', '?')}: модуль читает окружение по "
                f"ключу `{key}`. Ключ, который хост умеет прочитать, лежит на хосте — "
                f"вариант B перестаёт отличаться от варианта A.",
            ))

    # (2b) имя ENV-переменной с PRIVATE в любом литерале (кроме докстрок):
    #      ловит косвенность и «пока не используется, но уже заведено».
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if "PRIVATE" in text.upper() and _ENV_NAME_RE.fullmatch(text):
            bad.append((
                "env-имя-в-литерале",
                f"{filename}:{node.lineno}: литерал `{text}` — имя переменной окружения "
                f"с приватным ключом. Даже неиспользуемое, оно означает, что ключ "
                f"собираются класть на хост.",
            ))

    # (3) чтение файла, похожего на приватный ключ
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in _FILE_READERS:
            continue
        for text in _string_operands(node, consts):
            low = text.lower()
            if "priv" in low or "seckey" in low or low.endswith((".pem", ".key")):
                bad.append((
                    "чтение-файла",
                    f"{filename}:{node.lineno}: `{name}(...)` по пути `{text}` — модуль "
                    f"ищет приватный ключ на диске хоста.",
                ))
                break

    # (3b) путь, похожий на файл приватного ключа, в любом литерале:
    #      `Path(...) / 'backup_private.key'` не виден внутри вызова `.read_bytes()`.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if "priv" not in text.lower():
            continue
        if _PATHISH_RE.fullmatch(text) or text.lower().endswith((".pem", ".key")):
            bad.append((
                "путь-в-литерале",
                f"{filename}:{node.lineno}: литерал `{text}` выглядит файлом приватного "
                f"ключа. На хосте такого файла быть не должно ни под каким именем "
                f"(§7 п. 9).",
            ))

    return bad


def test_the_host_module_has_no_way_to_obtain_a_private_key():
    """Зелёная расшифровка на хосте — доказательство обратного тому, ради чего выбран B.

    Без этого сторожа `load_private_key()`, добавленный «чтобы еженедельный дрил
    §4.3 гонялся на хосте, а не на ноутбуке», не покрасит НИ ОДИН тест: поведение
    шифрования не меняется, все круговороты остаются зелёными. Меняется только
    модель угроз — молча и целиком: тот, кто забрал арендованный хост вместе с
    диском, забирает и переписку клиентов.
    """
    assert MODULE_PATH.exists(), (
        f"нет файла {MODULE_PATH} — сторожить нечего (модуль ещё не написан)."
    )
    violations = private_key_access_violations(
        MODULE_PATH.read_text(encoding="utf-8"), filename="app/services/backup_crypto.py"
    )
    assert not violations, (
        "в `backup_crypto.py` появился способ добыть ПРИВАТНЫЙ ключ на хосте:\n"
        + "\n".join(f"  [{rule}] {why}" for rule, why in violations)
        + "\nЕсли ключ понадобился для дрила — дрил живёт на ноутбуке владельца "
          "(§9.2), а хост читает его УЛИКУ, а не гоняет сам."
    )


# ── доказательство того, что структурный сторож вообще умеет краснеть ────────
_SAMPLE_BY_ENV = '''
"""Образец: ключ берётся прямо из окружения хоста."""
import os


def _material():
    return os.environ.get("JARVIS_BACKUP_PRIVATE_KEY")
'''

_SAMPLE_BY_ENV_INDIRECT = '''
"""Образец: то же самое, но через константу модуля."""
_PRIV_ENV = "JARVIS_BACKUP_PRIVATE_KEY"


def _material(env):
    return env.get(_PRIV_ENV)
'''

_SAMPLE_BY_FUNCTION_NAME = '''
"""Образец: функция-загрузчик приватного ключа."""


def load_private_key(env=None):
    return b"x" * 32
'''

_SAMPLE_BY_OPEN = '''
"""Образец: ключ читается с диска хоста."""


def _material():
    with open("C:/jarvis/.secrets/backup_private.key", "rb") as fh:
        return fh.read()
'''

_SAMPLE_BY_PATH = '''
"""Образец: путь собирается по кускам, вызов чтения литерала не содержит."""
from pathlib import Path


def _material(root):
    target = Path(root) / ".secrets" / "backup_privkey.pem"
    return target.read_bytes()
'''

_HONEST_SAMPLE = '''
"""Честный модуль варианта B.

Приватный ключ на хосте не появляется никогда; функция расшифровки есть,
но ключ ей передаёт машина владельца. Слова «приватный ключ» и "private
key" в прозе — это объяснение запрета, а не его нарушение.
"""
import os

PUBLIC_ENV = "JARVIS_BACKUP_PUBLIC_KEY"
SALT_ENV = "JARVIS_BACKUP_KEY_SALT"


class BackupCryptoError(Exception):
    """Отказ шифрования. Явный, не тихий (DEV-18)."""


def load_public_key(env=None):
    raw = (env if env is not None else os.environ).get(PUBLIC_ENV)
    if not raw:
        raise BackupCryptoError("нет JARVIS_BACKUP_PUBLIC_KEY")
    return raw.encode("ascii")


def decrypt_with(private_key, blob, *, aad):
    if len(private_key) != 32:
        raise BackupCryptoError("private key must be 32 bytes.")
    return blob
'''


@pytest.mark.parametrize(
    "source,expected_rule",
    [
        (_SAMPLE_BY_ENV, "env-ключ"),
        (_SAMPLE_BY_ENV_INDIRECT, "env-ключ"),
        (_SAMPLE_BY_FUNCTION_NAME, "имя-функции"),
        (_SAMPLE_BY_OPEN, "чтение-файла"),
        (_SAMPLE_BY_PATH, "путь-в-литерале"),
    ],
    ids=["os.environ", "через-константу", "имя-функции", "open()", "Path-литерал"],
)
def test_the_ast_guard_itself_goes_red_on_a_planted_violation(tmp_path, source, expected_rule):
    """Структурный сторож без этой проверки зелен ПО ПОСТРОЕНИЮ.

    Проверка «нарушений нет» проходит и на пустом файле, и на файле, который
    разбирается не так, как автор думал. Поэтому каждое правило доказывается
    на подсаженном образце: код с нарушением обязан покраснеть, и покраснеть
    ИМЕННО СВОИМ правилом.
    """
    sample = tmp_path / "sample_module.py"
    sample.write_text(source, encoding="utf-8")

    violations = private_key_access_violations(
        sample.read_text(encoding="utf-8"), filename="sample_module.py"
    )
    rules = {rule for rule, _why in violations}

    assert violations, (
        "образец с ЗАВЕДОМЫМ нарушением прошёл чисто — значит правило не работает "
        "и молчание на настоящем модуле ничего не доказывает.\nОбразец:\n" + source
    )
    assert expected_rule in rules, (
        f"образец покраснел, но не тем правилом: ожидалось `{expected_rule}`, "
        f"сработали {sorted(rules)}. Правило, которое ловит чужой случай, оставляет "
        f"свой непокрытым."
    )


def test_the_ast_guard_stays_green_on_an_honest_module(tmp_path):
    """Сторож, красный всегда, — не сторож, а фон.

    Честный модуль варианта B содержит и `decrypt_with(private_key, ...)`, и
    прозу про приватный ключ в докстроке, и сообщение об ошибке со словами
    "private key". Ни одно из этого нарушением не является: ключ приходит
    снаружи. Если сторож краснеет здесь, автор кода научится его отключать.
    """
    sample = tmp_path / "honest_module.py"
    sample.write_text(_HONEST_SAMPLE, encoding="utf-8")

    violations = private_key_access_violations(
        sample.read_text(encoding="utf-8"), filename="honest_module.py"
    )
    assert not violations, (
        "честный модуль объявлен нарушителем:\n"
        + "\n".join(f"  [{rule}] {why}" for rule, why in violations)
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. Чужой ключ не открывает
# ══════════════════════════════════════════════════════════════════════════════
def test_a_foreign_private_key_does_not_open_the_envelope(bc):
    """Иначе «зашифровано» означает «зашифровано на чей угодно ключ»."""
    _priv_a, pub_a = bc.generate_keypair()
    priv_b, _pub_b = bc.generate_keypair()
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"
    blob = bc.encrypt_for(pub_a, "история воронки клиента".encode("utf-8"), aad=aad)

    _expect_module_error(
        bc,
        "конверт для ключа A открыт ключом B",
        bc.decrypt_with, priv_b, blob, aad=aad,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. AAD привязывает конверт к МЕСТУ (§9.1)
# ══════════════════════════════════════════════════════════════════════════════
def test_the_envelope_is_bound_to_its_object_key_by_aad(bc):
    """Без привязки объект молча переставляется на другую дату или клиенту.

    Ключ объекта — `backups/client/<дата>/<псевдоним>/db`. Если он не входит в
    AAD, то тот, кто получил доступ к бакету, может переложить вчерашний
    конверт клиента A на сегодняшнее место клиента B: тег сойдётся,
    восстановление пройдёт, и в базе клиента B окажется чужая переписка.
    Красное здесь — единственное место, где такая подмена вообще заметна.
    """
    priv, pub = bc.generate_keypair()
    data = "реквизиты и переписка".encode("utf-8")
    here = b"backups/client/2026-08-22/0123456789abcdef/db"
    other_date = b"backups/client/2026-08-23/0123456789abcdef/db"
    other_client = b"backups/client/2026-08-22/fedcba9876543210/db"

    blob = bc.encrypt_for(pub, data, aad=here)
    assert bc.decrypt_with(priv, blob, aad=here) == data, (
        "конверт не открывается даже НА СВОЁМ месте — проверка подмены ниже "
        "тогда ничего не доказывает"
    )

    _expect_module_error(
        bc, "конверт переставлен на ДРУГУЮ ДАТУ и открылся",
        bc.decrypt_with, priv, blob, aad=other_date,
    )
    _expect_module_error(
        bc, "конверт переставлен ДРУГОМУ КЛИЕНТУ и открылся",
        bc.decrypt_with, priv, blob, aad=other_client,
    )
    _expect_module_error(
        bc, "конверт открылся с ПУСТЫМ aad",
        bc.decrypt_with, priv, blob, aad=b"",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Порча конверта ловится — ПЯТЬ отдельных случаев
# ══════════════════════════════════════════════════════════════════════════════
def _corruption_cases(bc) -> dict[str, tuple[int, str]]:
    magic = len(bc.MAGIC)
    return {
        "magic": (2, "подменён магический префикс — это уже не наш формат"),
        "версия": (magic, "подменён байт версии — конверт разбирается чужой раскладкой"),
        "эфемерный-ключ": (
            magic + 1 + 5,
            "испорчен эфемерный публичный ключ — сеансовый ключ выведется другой",
        ),
        "nonce": (magic + 1 + KEY_LEN + 3, "испорчен nonce"),
        "ciphertext": (magic + 1 + KEY_LEN + NONCE_LEN + 7, "испорчен сам шифротекст"),
    }


@pytest.mark.parametrize(
    "region",
    ["magic", "версия", "эфемерный-ключ", "nonce", "ciphertext"],
)
def test_a_single_flipped_bit_is_caught_wherever_it_lands(bc, region):
    """Пять случаев, а не один: они падают на РАЗНЫХ этапах разбора.

    Склеенный тест зеленеет от одной сработавшей проверки GCM и не заметит,
    что разбор заголовка перестал проверяться вовсе. Битая дорожка в бакете,
    прочитанная как «пустой набор», — это тихая потеря годового архива: объект
    есть, размер есть, содержимого нет.
    """
    priv, pub = bc.generate_keypair()
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"
    blob = bc.encrypt_for(pub, b"payload" * 32, aad=aad)
    index, why = _corruption_cases(bc)[region]
    assert index < len(blob), f"смещение {index} вне конверта длиной {len(blob)}"

    _expect_module_error(
        bc, f"перевёрнут бит в области `{region}` ({why}), а конверт открылся",
        bc.decrypt_with, priv, _flip_bit(blob, index), aad=aad,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. Обрезанный конверт
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "cut",
    ["пусто", "только-magic", "заголовок-минус-байт", "ровно-заголовок"],
)
def test_a_truncated_envelope_fails_with_the_module_error_not_from_the_library(bc, cut):
    """Оборванная загрузка обязана называться отказом, а не падать из недр.

    Объект, недокачанный из R2, — обычное дело. Если разбор заголовка режет
    срезами без проверки длины, наружу вылетит `InvalidTag` или пустой
    результат — и конвейер восстановления сообщит «файл пустой» вместо
    «объект битый, бери вчерашний».
    """
    priv, pub = bc.generate_keypair()
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"
    blob = bc.encrypt_for(pub, "данные".encode("utf-8"), aad=aad)
    head = _header_len(bc)
    truncated = {
        "пусто": b"",
        "только-magic": bc.MAGIC,
        "заголовок-минус-байт": blob[: head - 1],
        "ровно-заголовок": blob[:head],
    }[cut]

    _expect_module_error(
        bc, f"обрезанный конверт ({cut}, {len(truncated)} байт) не отвергнут",
        bc.decrypt_with, priv, truncated, aad=aad,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Два шифрования одних данных дают РАЗНЫЕ байты
# ══════════════════════════════════════════════════════════════════════════════
def test_encrypting_the_same_data_twice_never_yields_the_same_bytes(bc):
    """Детерминированный конверт показывает читателю бакета, что не менялось.

    Если вчерашний и сегодняшний объекты клиента побайтово равны, тот, кто
    видит только бакет, читает: «в этом бизнесе за сутки не было ни одного
    сообщения». Это утечка содержимого без единой расшифровки. Хуже того,
    повторный nonce на одном ключе ломает GCM целиком.
    """
    _priv, pub = bc.generate_keypair()
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"
    data = b"one and the same data"
    first = bc.encrypt_for(pub, data, aad=aad)
    second = bc.encrypt_for(pub, data, aad=aad)

    assert first != second, (
        "два шифрования одних данных дали ОДИН И ТОТ ЖЕ конверт: эфемерная пара "
        "и/или nonce фиксированы."
    )
    magic = len(bc.MAGIC)
    eph = slice(magic + 1, magic + 1 + KEY_LEN)
    nonce = slice(magic + 1 + KEY_LEN, magic + 1 + KEY_LEN + NONCE_LEN)
    assert first[eph] != second[eph], "эфемерный публичный ключ повторился — пара не эфемерная"
    assert first[nonce] != second[nonce], (
        "nonce повторился при том же получателе — повторный (ключ, nonce) в AES-GCM "
        "раскрывает XOR открытых текстов и убивает подлинность"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. Фейл-клоуз загрузчиков
# ══════════════════════════════════════════════════════════════════════════════
_BAD_KEY_ENVS = [
    ({}, "переменной нет"),
    ({PUBLIC_KEY_ENV: ""}, "переменная пустая"),
    ({PUBLIC_KEY_ENV: "   "}, "одни пробелы"),
    ({PUBLIC_KEY_ENV: "not base64!!"}, "не base64"),
    ({PUBLIC_KEY_ENV: "abc"}, "битая набивка base64"),
    ({PUBLIC_KEY_ENV: "@@@@"}, "лениво декодируется в пусто"),
    ({PUBLIC_KEY_ENV: _b64(b"k" * 31)}, "31 байт вместо 32"),
    ({PUBLIC_KEY_ENV: _b64(b"k" * 33)}, "33 байта вместо 32"),
]


@pytest.mark.parametrize("env,case", _BAD_KEY_ENVS, ids=[c for _e, c in _BAD_KEY_ENVS])
def test_load_public_key_is_fail_closed(bc, env, case):
    """Загрузчик, вернувший None, превращается в «пишем бэкап без шифрования».

    Именно так дефект и выглядел бы живьём: переменная не доехала на новую
    машину после переезда в облако, `load_public_key` вернул None, вызывающий
    код проверил `if key:` — и годовой набор поехал в бакет открытым текстом.
    Ни одна проба этого не увидит: объект есть, размер правильный.
    """
    _expect_module_error(bc, f"публичный ключ ({case}) принят", bc.load_public_key, env=env)


def test_load_public_key_accepts_a_correct_key_and_returns_raw_bytes(bc):
    """Обратная сторона: загрузчик, красный всегда, тоже не даст писать бэкап."""
    raw = bytes(range(32))
    got = bc.load_public_key(env={PUBLIC_KEY_ENV: _b64(raw)})
    assert got == raw, (
        f"загрузчик вернул {got!r} вместо исходных 32 байт — по контракту наружу "
        f"выходит СЫРОЙ ключ, а не base64 и не объект библиотеки"
    )


_BAD_SALT_ENVS = [
    ({}, "переменной нет"),
    ({SALT_ENV: ""}, "переменная пустая"),
    ({SALT_ENV: "not base64!!"}, "не base64"),
    ({SALT_ENV: "abc"}, "битая набивка base64"),
    ({SALT_ENV: "@@@@"}, "лениво декодируется в пусто"),
    ({SALT_ENV: _b64(b"s" * 15)}, "15 байт при минимуме 16"),
]


@pytest.mark.parametrize("env,case", _BAD_SALT_ENVS, ids=[c for _e, c in _BAD_SALT_ENVS])
def test_load_pseudonym_salt_is_fail_closed(bc, env, case):
    """Соль-умолчание («если нет — возьмём пустую») делает псевдонимы общими.

    Псевдоним на пустой или предсказуемой соли подбирается по списку слагов
    за секунды: §9.1 защищает от читателя бакета, а с известной солью читатель
    бакета снова видит имена чужих бизнесов.
    """
    _expect_module_error(bc, f"соль ({case}) принята", bc.load_pseudonym_salt, env=env)


@pytest.mark.parametrize("size", [16, 32], ids=["16Б-минимум", "32Б"])
def test_load_pseudonym_salt_accepts_a_long_enough_salt(bc, size):
    """Иначе «фейл-клоуз» неотличим от «сломано насовсем»."""
    raw = bytes(range(size))
    got = bc.load_pseudonym_salt(env={SALT_ENV: _b64(raw)})
    assert got == raw, f"соль вернулась как {got!r} вместо {size} исходных байт"


# ══════════════════════════════════════════════════════════════════════════════
# 9–10. Псевдоним (§7 п. 8, §9.1)
# ══════════════════════════════════════════════════════════════════════════════
SLUGS = ("volska", "yarina", "olga-dental", "volsk")


def test_the_pseudonym_is_stable_for_the_same_slug_and_salt(bc):
    """Нестабильный псевдоним теряет объект: вчерашнего в бакете «не существует».

    По ключу объекта ходят трое: `verify_uploaded` сверяет ожидаемые имена,
    ротация решает, что удалять, дрил ищет вчерашний объект. Если псевдоним
    пересчитывается заново каждый раз (соль подмешана временем, взят random),
    то ротация не найдёт старьё и оно будет копиться, verify скажет «объект не
    доехал» на успешной загрузке, а дрил не найдёт что восстанавливать.
    """
    salt = b"S" * 16
    first = bc.pseudonym("volska", salt)
    second = bc.pseudonym("volska", salt)
    assert first == second, (
        f"два вызова подряд дали РАЗНЫЕ псевдонимы ({first} != {second}) — в ключе "
        f"объекта появляется случайность, и вчерашний объект найти нечем"
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_the_pseudonym_is_sixteen_lowercase_hex_characters(bc, slug):
    """Длина и алфавит — часть ИМЕНИ объекта; сюрприз тут = сломанный ключ."""
    value = bc.pseudonym(slug, b"S" * 16)
    assert isinstance(value, str), f"псевдоним вернулся как {type(value).__name__}, а не str"
    assert len(value) == 16, f"длина псевдонима {len(value)} вместо 16: `{value}`"
    assert set(value) <= HEX, (
        f"псевдоним `{value}` содержит символы вне [0-9a-f]. В ключ объекта едет "
        f"строка: любой другой символ — это вопрос экранирования в URL и разный "
        f"регистр в разных местах."
    )


def test_different_slugs_never_share_a_pseudonym_under_one_salt(bc):
    """Слипшиеся псевдонимы = набор одного клиента затирает набор другого."""
    salt = b"S" * 16
    seen: dict[str, str] = {}
    for slug in SLUGS:
        value = bc.pseudonym(slug, salt)
        clash = [known for known, other in seen.items() if other == value]
        assert not clash, (
            f"слаги `{slug}` и `{clash[0]}` дали ОДИН псевдоним `{value}` — их объекты "
            f"лягут по одному ключу, и один клиент затрёт другого"
        )
        seen[slug] = value


def test_different_salts_never_share_a_pseudonym_for_one_slug(bc):
    """Если соль ни на что не влияет, псевдоним подбирается словарём слагов."""
    a = bc.pseudonym("volska", b"A" * 16)
    b = bc.pseudonym("volska", b"B" * 16)
    assert a != b, (
        f"смена соли не изменила псевдоним (`{a}`) — соль в вычислении не участвует, "
        f"и любой, кто видит бакет, восстанавливает имена бизнесов перебором слагов"
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_the_pseudonym_does_not_leak_the_slug_itself(bc, slug):
    """Псевдоним, содержащий слаг, — это тот же адрес, надписанный снаружи (§9.1)."""
    value = bc.pseudonym(slug, b"S" * 16)
    assert slug.lower() not in value.lower(), (
        f"слаг `{slug}` виден в псевдониме `{value}` целиком"
    )
    assert slug.encode("utf-8").hex() not in value.lower(), (
        f"слаг `{slug}` лежит в псевдониме `{value}` в hex-виде своих байт"
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_the_pseudonym_is_not_an_unsalted_digest(bc, slug):
    """Несолёный хеш от слага подбирается словарём мгновенно.

    Список слагов невелик и предсказуем (имена бизнесов). Если псевдоним —
    это просто sha256 от слага, читатель бакета берёт свой список и за секунду
    получает соответствие «объект → чей бизнес». Именно от этого ответ 4 и
    §9.1 защищают, и именно это выглядит «зашифрованным» при взгляде на код.
    """
    value = bc.pseudonym(slug, b"S" * 16)
    raw = slug.encode("utf-8")
    unsalted = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sha1": hashlib.sha1(raw).hexdigest(),
        "md5": hashlib.md5(raw).hexdigest(),
        "blake2b": hashlib.blake2b(raw).hexdigest(),
        "hmac-с-пустой-солью": hmac.new(b"", raw, hashlib.sha256).hexdigest(),
    }
    for name, digest in unsalted.items():
        assert value != digest[:16], (
            f"псевдоним слага `{slug}` равен несолёному {name}[:16] — соль в "
            f"вычислении не участвует, псевдоним подбирается по словарю слагов"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 11. Отпечаток публичного ключа
# ══════════════════════════════════════════════════════════════════════════════
def test_the_public_key_fingerprint_is_deterministic_and_distinguishes_keys(bc):
    """Без отпечатка смена ключа обнаружится на дриле — то есть через год.

    Отпечаток — единственная дешёвая улика того, НА ЧЕЙ ключ писались объекты.
    Если владелец сменил пару, а на хост доехала старая переменная, объекты
    год пишутся на ключ, которого у владельца больше нет. Расшифровка при
    этом не пробуется НИ РАЗУ: хост по построению не умеет читать (§9.2).
    """
    keys = [bc.generate_keypair()[1] for _ in range(5)]
    fingerprints = [bc.public_key_fingerprint(k) for k in keys]

    for key, fingerprint in zip(keys, fingerprints):
        assert bc.public_key_fingerprint(key) == fingerprint, (
            "два вызова на одном ключе дали разные отпечатки — сравнивать нечего"
        )
        assert isinstance(fingerprint, str), f"отпечаток — {type(fingerprint).__name__}, а не str"
        assert len(fingerprint) == 16, f"длина отпечатка {len(fingerprint)} вместо 16"
        assert set(fingerprint) <= HEX, f"отпечаток `{fingerprint}` не hex"

    assert len(set(fingerprints)) == len(fingerprints), (
        f"разные ключи дали одинаковые отпечатки: {fingerprints}. Отпечаток, "
        f"не различающий ключи, зелен на подменённом ключе."
    )


# ══════════════════════════════════════════════════════════════════════════════
# 12. Потолок открытого текста
# ══════════════════════════════════════════════════════════════════════════════
def test_data_larger_than_the_ceiling_is_refused(bc, monkeypatch):
    """Без потолка выросшая база кладёт хост, а бэкап не пишется ВООБЩЕ.

    Настоящие 512 МиБ здесь не выделяются намеренно: проверяется механика
    (потолок читается на вызове и сравнивается с длиной), а не память машины.
    Если этот тест красный при живом коде — константа прочитана один раз при
    импорте (умолчание аргумента, замороженная копия), и поднять потолок на
    работающем хосте можно будет только правкой кода.
    """
    _priv, pub = bc.generate_keypair()
    monkeypatch.setattr(bc, "MAX_PLAINTEXT_BYTES", 64)
    aad = b"backups/client/2026-08-22/0123456789abcdef/db"

    ok = bc.encrypt_for(pub, b"x" * 64, aad=aad)
    assert ok, "ровно потолок обязан пройти: отказ на границе — это отказ на всём"

    _expect_module_error(
        bc, "открытый текст ВЫШЕ потолка принят",
        bc.encrypt_for, pub, b"x" * 65, aad=aad,
    )
