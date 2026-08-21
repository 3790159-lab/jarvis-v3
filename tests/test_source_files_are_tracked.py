# -*- coding: utf-8 -*-
"""Сторож на КЛАСС: исходник лежит на диске, работает в тестах — и НЕВИДИМ для git.

ПОВОД (21.08, поймано живьём). В `.gitignore` с давних пор живёт строка
`backup_*.py` — она писалась против случайных локальных копий скриптов
(`backup_старое_имя.py`). В арке DEV-46 появился модуль шифрования
`app/services/backup_crypto.py`, и правило проглотило его ПО ИМЕНИ:

    $ git check-ignore -v app/services/backup_crypto.py
    .gitignore:21:backup_*.py	app/services/backup_crypto.py

Файл был на диске (23 861 Б), тесты по нему зеленели, `git status` молчал.
Мерж уехал бы БЕЗ модуля шифрования, и вскрылось бы это на деплое.

КЛАСС, А НЕ СЛУЧАЙ. Опасна не строка `backup_*.py`, а сам механизм: маска в
`.gitignore` пишется под одну цель (мусор), а срабатывает по имени — то есть
на всём, что под имя подошло, включая исходник, которого тогда ещё не было.
Ни один существующий сигнал этого не ловит: тесты зелёные (файл НА ДИСКЕ),
`git status` чистый (файл невидим), гейт зелёный. Тишина — и есть дефект.

ЧЕМ ОТЛИЧАЕТСЯ ОТ ЛОКАЛЬНОГО ЧЕРНОВИКА. Проигнорированный `.py` сам по себе
не беда: в живом дереве их сегодня двенадцать — `scripts/_*.py` и
`scripts/debug_*.py`, ровно тот мусор, ради которого маски и написаны. Если
краснеть на всяком проигнорированном `.py`, сторож будет красным ВСЕГДА в
живом дереве и его снимут через неделю. Поэтому разделение сделано по
МЕХАНИЗМУ, а не по списку разрешённых имён (см. `classify_invisible_sources`).
Красное, если верно ХОТЯ БЫ ОДНО:

  (A) файл лежит в пакетном дереве `app/`, `chatter/`, `tools/` — черновикам
      там не место, и сегодня их там НОЛЬ во всех трёх деревьях;
  (B) на файл УЖЕ ССЫЛАЕТСЯ код, видимый гиту: на него опираются, а мерж
      уедет без него. Ровно этот признак был у ночного случая —
      `tests/test_backup_crypto.py` импортировал проглоченный модуль;
  (C) съевшая маска НЕ ПРИВЯЗАНА К ПУТИ (в шаблоне нет `/`), то есть ловит по
      ИМЕНИ на любой глубине.

ПРИЗНАК (C) ДОБАВЛЕН НЕ ИЗ ГОЛОВЫ — он спасает от второго живого экземпляра.
Пока писался этот сторож, версия с одними (A)+(B) была прогнана по трём
деревьям и в `dev46-impl` нашла ВТОРОЙ проглоченный файл той же ночи:
`scripts/backup_keygen.py` (25 265 Б, инструмент владельца DEV-46 §3.3 B) —
съеден той же строкой `backup_*.py`. Он лежит вне пакетных деревьев, и на него
никто не ссылается (это одноразовая ручная команда), поэтому (A)+(B) его
пропускали — сторож был бы ЗЕЛЁН на настоящем дефекте.

Различает же их именно привязка маски к пути: `backup_*.py` — голое имя,
действующее по всему дереву, и автор такой строки НЕ МОГ знать, что там
появится потом. А `scripts/_*.py` и `scripts/debug_*.py` написаны под
конкретный каталог и конкретное соглашение об именах черновиков. Отсюда и
починка, которую предлагает сообщение: не «удалить правило», а ПРИВЯЗАТЬ его
к месту.

Остальное (черновик, съеденный привязанной к каталогу маской, вне пакетных
деревьев, без единой ссылки) выводится через `warnings.warn` — pytest печатает
такие в сводке, так что оно НЕ молчит, но и не блокирует.

ССЫЛКИ ИЩУТСЯ СРЕДИ ВИДИМЫХ ФАЙЛОВ, А НЕ СРЕДИ ОТСЛЕЖИВАЕМЫХ. Тонкость,
которая решает, сработал бы сторож ночью: `tests/test_backup_crypto.py` в тот
момент сам был новым и НЕотслеживаемым. Ищи мы ссылки только в `git ls-files`,
сторож промолчал бы на настоящем дефекте. Поэтому корпус ссылок — все `.py` и
`.ps1` из дерева, КРОМЕ проигнорированных: «видим git'у» = «уедет в мерж».

ВТОРОЙ СТОРОЖ В ЭТОМ ЖЕ ФАЙЛЕ (BOM) — см. `test_bom_py_count_does_not_grow`.

ЧТО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ. Не чинит `.gitignore` (конкретный случай закрыт
отдельной правкой другого автора) и не судит о том, ПРАВИЛЬНАЯ ли маска: он
судит только о наблюдаемом факте «исходник невидим».
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import NamedTuple

import pytest

# --- настоящие каталоги исходников -----------------------------------------
# Кандидаты — то, что обязано доезжать до мержа как код.
SOURCE_DIRS = ("app", "chatter", "scripts", "tools")
# Пакетные деревья: проигнорированный `.py` здесь — красное безусловно (правило A).
PACKAGE_DIRS = ("app", "chatter", "tools")
# Где ищем ССЫЛКИ на кандидата. `tests/` обязателен: ночью ссылался именно тест.
REFERENCE_DIRS = ("app", "chatter", "scripts", "tools", "tests")
REFERENCE_SUFFIXES = (".py", ".ps1")

# --- BOM: ЗАМЕР, а не поломка ----------------------------------------------
# Замерено 22.08.2026 на трёх деревьях (C:\jarvis, dev46-impl, dev46-guards):
# во всех трёх ровно 137 файлов `.py` с UTF-8 BOM среди НЕ проигнорированных
# в `app/ chatter/ scripts/ tools/ tests/` (app 106, chatter 0, scripts 7,
# tools 24, tests 0).
# Почему фильтр по игнору: в живом дереве лежат ещё два локальных черновика
# (`scripts/debug_*.py`) с BOM, и без фильтра число прыгало бы 137/139 в
# зависимости от машины — сторож краснел бы от чужого мусора.
BOM_PY_FILE_CAP = 137


# ---------------------------------------------------------------------------
# помощники (одни и те же для живого дерева и для подставного из tmp_path)
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Корень дерева, в котором лежит этот тест."""
    return Path(__file__).resolve().parent.parent


def require_git(root: Path, env: dict | None = None) -> str:
    """Вернуть путь к git или ПРОПУСТИТЬ тест с внятной причиной.

    «Не смогли проверить» и «проверили, всё чисто» — разные вещи; склеивать их
    в зелёное нельзя, поэтому здесь skip, а не молчаливый успех.
    """
    git = shutil.which("git")
    if git is None:
        pytest.skip("git не найден в PATH — проверить видимость файлов нечем")
    probe = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"],
        cwd=str(root),
        capture_output=True,
        env=env,
    )
    if probe.returncode != 0:
        pytest.skip(f"{root} не является git-репозиторием — проверять нечем")
    return git


def list_py_files(root: Path, dirs) -> list[str]:
    """Относительные posix-пути всех `.py` в перечисленных каталогах."""
    found: list[str] = []
    for name in dirs:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path.is_file():
                found.append(path.relative_to(root).as_posix())
    return sorted(set(found))


def _reference_files(root: Path) -> list[str]:
    """Все `.py`/`.ps1` из каталогов, где может лежать ссылка на кандидата."""
    found: list[str] = []
    for name in REFERENCE_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES:
                found.append(path.relative_to(root).as_posix())
    return sorted(set(found))


class IgnoreRule(NamedTuple):
    """Строка .gitignore, которая съела файл: где написана и что за шаблон."""

    source: str   # файл правил, например `.gitignore`
    line: str     # номер строки в нём
    pattern: str  # сам шаблон, например `backup_*.py`

    def __str__(self) -> str:
        # Ровно тот формат, что печатает `git check-ignore -v`.
        return f"{self.source}:{self.line}:{self.pattern}"

    @property
    def anchored(self) -> bool:
        """Привязана ли маска к пути.

        Шаблон со `/` внутри git применяет только к тому месту, где он написан;
        шаблон без `/` — к ЛЮБОМУ файлу с таким именем на любой глубине. Это и
        есть разница между «черновики в этом каталоге» (`scripts/_*.py`) и
        «всё, что похоже на имя» (`backup_*.py`).

        Срезается ТОЛЬКО хвостовой слэш: он означает «это каталог» и места не
        задаёт (`build/` ловит любой `build` на любой глубине). Ведущий слэш,
        наоборот, привязывает к корню репозитория (`/.secrets/`) — срезать его
        нельзя. На этом первая версия и ошиблась: `strip("/")` съедал оба, и
        `/.secrets/` числился голой маской.
        """
        return "/" in self.pattern.rstrip("/")


def ignored_with_rule(root: Path, rel_paths, env: dict | None = None) -> dict[str, IgnoreRule]:
    """{путь: 'источник:строка:шаблон'} для файлов, ДЕЙСТВИТЕЛЬНО игнорируемых.

    ОДИН вызов `git check-ignore --stdin -z -v` на весь список — по вызову на
    файл это сотни процессов и десятки секунд.

    ГЛАВНАЯ ТОНКОСТЬ. Код возврата 0 у `check-ignore` означает «шаблон СОВПАЛ»,
    а не «файл игнорируется»: последним совпавшим может оказаться правило-
    ИСКЛЮЧЕНИЕ (`!путь`), и тогда файл как раз НЕ игнорируется. Проверено на
    живом дереве обеими сторонами одним вызовом — один код возврата на две
    строки с противоположным смыслом:

        .gitignore:90:.env.*                    .env.foo
        .gitignore:107:!requisites.example.yaml requisites.example.yaml
        exit 0

    Поэтому решает РАЗБОР вывода: файл проигнорирован только если последний
    совпавший шаблон не начинается с `!`.

    `-z` выбран вместо разбора `-v`-строк намеренно: поля разделены NUL, и
    разбор не ломается ни о `:` в пути, ни о таб внутри шаблона.

    Отслеживаемые файлы `check-ignore` не показывает вовсе (индекс важнее
    масок) — и это ровно то, что нужно: файл в индексе ВИДЕН git'у, дефекта
    нет, каким бы маскам он ни соответствовал.
    """
    if not rel_paths:
        return {}
    git = require_git(root, env)
    payload = ("\0".join(rel_paths) + "\0").encode("utf-8", "surrogateescape")
    proc = subprocess.run(
        [git, "check-ignore", "--stdin", "-z", "-v"],
        cwd=str(root),
        input=payload,
        capture_output=True,
        env=env,
    )
    # 0 — что-то совпало, 1 — ничего не совпало; всё остальное — сбой git,
    # и глотать его нельзя: это «не проверили», а не «чисто».
    if proc.returncode not in (0, 1):
        raise AssertionError(
            "git check-ignore завершился с кодом "
            f"{proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    fields = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    ignored: dict[str, IgnoreRule] = {}
    # Формат при `-z -v`: <источник>\0<строка>\0<шаблон>\0<путь>\0
    for i in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[i : i + 4]
        if pattern.startswith("!"):
            continue  # правило-ИСКЛЮЧЕНИЕ: файл виден git'у, это не находка
        ignored[path] = IgnoreRule(source, line, pattern)
    return ignored


def _reference_patterns(rel_path: str) -> list[re.Pattern]:
    """Как выглядит ССЫЛКА на этот файл в чужом исходнике.

    Узко и по делу: голое имя модуля даёт ложные совпадения на общих словах
    (`main`, `config`, `utils`), поэтому ищем импорт или путь, а не просто
    слово. Ночной случай ловится сразу двумя образцами: `tests/
    test_backup_crypto.py` содержит и `from app.services import backup_crypto`,
    и литеральный путь `app/services/backup_crypto.py`.
    """
    posix = rel_path
    stem = Path(rel_path).stem
    basename = Path(rel_path).name
    dotted = rel_path[: -len(".py")].replace("/", ".")
    parent_dotted = str(Path(rel_path).parent).replace("\\", "/").replace("/", ".")
    esc_stem = re.escape(stem)
    patterns = [
        # app.services.backup_crypto
        rf"(?<![\w.]){re.escape(dotted)}(?![\w])",
        # import backup_crypto / from backup_crypto import ...
        rf"(?:^|[^\w.])import\s+{esc_stem}(?![\w])",
        rf"(?:^|[^\w.])from\s+{esc_stem}(?![\w])\s+import",
        # from app.services import backup_crypto
        rf"from\s+{re.escape(parent_dotted)}\s+import[^\n]*?(?<![\w.]){esc_stem}(?![\w])",
        # "app/services/backup_crypto.py", 'app\services\backup_crypto.py'
        re.escape(posix),
        re.escape(posix.replace("/", "\\")),
        # "backup_crypto.py" — запуск скрипта из .ps1 и прочие пути
        rf"(?<![\w.]){re.escape(basename)}",
    ]
    return [re.compile(p.encode("utf-8"), re.MULTILINE) for p in patterns]


def _reference_corpus(root: Path, ignored: dict) -> dict[str, bytes]:
    """Файлы, ВИДИМЫЕ git'у: они и уедут в мерж, их ссылки и считаются."""
    corpus: dict[str, bytes] = {}
    for rel in _reference_files(root):
        if rel in ignored:
            continue
        try:
            corpus[rel] = (root / rel).read_bytes()
        except OSError:
            continue
    return corpus


def classify_invisible_sources(root: Path, env: dict | None = None):
    """(жёсткие находки, мягкие находки) для дерева `root`.

    Жёсткая находка — `(путь, правило, [кто ссылается])`, ради неё сторож и
    существует. Мягкая — `(путь, правило)`: проигнорированный черновик вне
    пакетных деревьев, на который никто из видимого кода не ссылается.
    """
    candidates = list_py_files(root, SOURCE_DIRS)
    everything = sorted(set(candidates) | set(_reference_files(root)))
    ignored = ignored_with_rule(root, everything, env)
    corpus = _reference_corpus(root, ignored)

    hard: list[tuple[str, IgnoreRule, list[str]]] = []
    soft: list[tuple[str, IgnoreRule]] = []
    for rel in candidates:
        rule = ignored.get(rel)
        if rule is None:
            continue
        patterns = _reference_patterns(rel)
        referers = sorted(
            other
            for other, blob in corpus.items()
            if any(pattern.search(blob) for pattern in patterns)
        )
        in_package_tree = rel.split("/", 1)[0] in PACKAGE_DIRS
        if in_package_tree or referers or not rule.anchored:
            hard.append((rel, rule, referers))
        else:
            soft.append((rel, rule))
    return hard, soft


def describe_findings(hard) -> str:
    """Сообщение сторожа: виноватый НАЗВАН путём И правилом, которое его съело."""
    lines = [
        "Исходники лежат на диске, но НЕВИДИМЫ для git — мерж уедет без них, молча:",
        "",
    ]
    for rel, rule, referers in hard:
        lines.append(f"  {rel}")
        lines.append(f"      съеден правилом: {rule}")
        reasons = []
        if rel.split("/", 1)[0] in PACKAGE_DIRS:
            reasons.append("лежит в пакетном дереве, где черновиков быть не должно")
        if not rule.anchored:
            reasons.append(
                f"маска `{rule.pattern}` не привязана к пути — ловит по ИМЕНИ "
                "на любой глубине"
            )
        if referers:
            shown = ", ".join(referers[:5])
            more = f" (и ещё {len(referers) - 5})" if len(referers) > 5 else ""
            reasons.append(f"на него уже ссылаются: {shown}{more}")
        for reason in reasons:
            lines.append(f"      почему красное: {reason}")
    lines += [
        "",
        "Проверить руками:  git check-ignore -v <путь>",
        "Починка — ПРИВЯЗАТЬ маску к месту (`scripts/backup_*.py` вместо",
        "`backup_*.py`) или явно вернуть файл строкой-исключением `!<путь>`.",
        "Тесты по такому файлу ЗЕЛЁНЫЕ (он на диске), git status ЧИСТЫЙ —",
        "поэтому, кроме этого сторожа, поймать нечем.",
    ]
    return "\n".join(lines)


def bom_py_files(root: Path, env: dict | None = None) -> list[str]:
    """`.py` с UTF-8 BOM среди ВИДИМЫХ git'у файлов, отсортированные."""
    candidates = list_py_files(root, REFERENCE_DIRS)
    ignored = ignored_with_rule(root, candidates, env)
    found: list[str] = []
    for rel in candidates:
        if rel in ignored:
            continue
        try:
            with open(root / rel, "rb") as handle:
                head = handle.read(3)
        except OSError:
            continue
        if head == b"\xef\xbb\xbf":
            found.append(rel)
    return found


# ---------------------------------------------------------------------------
# сторож 1: исходник невидим для git
# ---------------------------------------------------------------------------


def test_no_source_file_is_invisible_to_git():
    """НИ ОДИН `.py` из `app/ chatter/ scripts/ tools/` не проглочен .gitignore.

    На дереве, где `.gitignore` здоров, тест зелёный ПО ПОСТРОЕНИЮ — поэтому
    его годность доказана отдельно, на подставном дереве:
    `test_guard_reddens_on_a_swallowed_module`.
    """
    root = repo_root()
    require_git(root)
    hard, soft = classify_invisible_sources(root)

    for rel, rule in soft:
        warnings.warn(
            f"проигнорированный черновик: {rel} (правило {rule}); "
            "ссылок из видимого кода нет — не блокирую, но знай, что он есть",
            UserWarning,
            stacklevel=1,
        )

    assert not hard, describe_findings(hard)


# ---------------------------------------------------------------------------
# краснение сторожа: подставные деревья в tmp_path
# ---------------------------------------------------------------------------


def _init_fake_repo(tmp_path: Path):
    """Крошечный git-репозиторий, изолированный от глобального конфига."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git не найден в PATH")
    root = tmp_path / "fake_repo"
    root.mkdir()
    env = dict(os.environ)
    # Чужой core.excludesFile добавил бы правил и сделал бы результат
    # зависящим от машины, на которой гоняют сторожа.
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "no-global-config")
    env["GIT_CONFIG_SYSTEM"] = str(tmp_path / "no-system-config")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    subprocess.run(
        [git, "init", "-q"], cwd=str(root), env=env, capture_output=True, check=True
    )
    return root, env


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_guard_reddens_on_a_swallowed_module(tmp_path):
    """Сторож ОБЯЗАН назвать и файл, и правило, которое его съело.

    Воспроизводим ночной случай в чистом виде: маска `backup_*.py`, модуль
    `app/services/backup_thing.py` на диске, тест на него ссылается.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "backup_*/\nbackup_*.py\n")
    _write(root, "app/services/backup_thing.py", "SECRET_BOX = object()\n")
    _write(root, "app/services/plain.py", "VALUE = 1\n")
    _write(
        root,
        "tests/test_backup_thing.py",
        "from app.services import backup_thing\n\n\ndef test_x():\n    assert backup_thing\n",
    )

    hard, soft = classify_invisible_sources(root, env)

    assert [rel for rel, _, _ in hard] == ["app/services/backup_thing.py"], hard
    assert soft == []

    rel, rule, referers = hard[0]
    assert str(rule) == ".gitignore:2:backup_*.py", rule
    assert rule.pattern == "backup_*.py"
    assert not rule.anchored, "маска без `/` действует на любой глубине"

    assert "tests/test_backup_thing.py" in referers, referers

    message = describe_findings(hard)
    assert "app/services/backup_thing.py" in message
    assert "backup_*.py" in message
    assert ".gitignore:2" in message


def test_negation_rule_is_not_an_ignore(tmp_path):
    """Правило-ИСКЛЮЧЕНИЕ не должно считаться игнором.

    `git check-ignore` возвращает 0 и на исключение тоже — сторож, смотрящий
    на код возврата, покраснел бы на ЗДОРОВОМ файле. Проверяем обе стороны в
    одном дереве: съеденный файл найден, возвращённый исключением — нет.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "backup_*.py\n!app/services/backup_kept.py\n")
    _write(root, "app/services/backup_eaten.py", "A = 1\n")
    _write(root, "app/services/backup_kept.py", "B = 2\n")

    paths = ["app/services/backup_eaten.py", "app/services/backup_kept.py"]
    ignored = ignored_with_rule(root, paths, env)

    assert str(ignored.get("app/services/backup_eaten.py")) == ".gitignore:1:backup_*.py"
    assert "app/services/backup_kept.py" not in ignored, (
        "файл возвращён строкой-исключением `!...` — он ВИДИМ git'у; "
        f"разбор вывода check-ignore сломан: {ignored}"
    )

    hard, _ = classify_invisible_sources(root, env)
    assert [rel for rel, _, _ in hard] == ["app/services/backup_eaten.py"]


def test_healthy_tree_stays_green(tmp_path):
    """Обратная сторона: на здоровом дереве сторож молчит, а не краснеет всегда."""
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "*.log\n__pycache__/\n")
    _write(root, "app/services/crypto.py", "A = 1\n")
    _write(root, "scripts/tool.py", "B = 2\n")

    hard, soft = classify_invisible_sources(root, env)
    assert hard == []
    assert soft == []


def test_unanchored_mask_outside_packages_is_hard(tmp_path):
    """ВТОРОЙ живой экземпляр: инструмент, на который никто не ссылается.

    `scripts/backup_keygen.py` из `dev46-impl` съеден той же строкой
    `backup_*.py`, лежит вне пакетных деревьев и не импортируется ниоткуда —
    это одноразовая ручная команда владельца. Признаки (A) и (B) его
    пропускали, и сторож был бы ЗЕЛЁН на настоящем дефекте.

    Ловит его признак (C): маска без `/` действует на любой глубине, и автор
    такой строки не мог знать, что под неё попадёт исходник.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "backup_*.py\n")
    _write(root, "scripts/backup_keygen.py", "def main():\n    return 0\n")

    hard, soft = classify_invisible_sources(root, env)

    assert [rel for rel, _, _ in hard] == ["scripts/backup_keygen.py"], hard
    assert soft == []
    rel, rule, referers = hard[0]
    assert referers == [], "ссылок и не должно быть — в этом вся ловушка"
    assert not rule.anchored

    message = describe_findings(hard)
    assert "scripts/backup_keygen.py" in message
    assert "backup_*.py" in message
    assert "не привязана к пути" in message


def test_anchored_and_unanchored_masks_are_told_apart():
    """Признак (C) — про ШАБЛОН, и проверяется на шаблонах напрямую.

    Литеральный список, а не выборка из кода: выведенный из реализации список
    согласен с ней по определению и промолчит ровно там, где она забыла.
    """
    anchored = ["scripts/_*.py", "scripts/debug_*.py", "chatter/clients/*/.versions/",
                "/.secrets/", "data/test_inputs/"]
    unanchored = ["backup_*.py", "*.log", "requisites.yaml", "entropy.bin", "build/"]

    for pattern in anchored:
        assert IgnoreRule(".gitignore", "1", pattern).anchored, pattern
    for pattern in unanchored:
        assert not IgnoreRule(".gitignore", "1", pattern).anchored, pattern


def test_unreferenced_scratch_outside_packages_is_soft(tmp_path):
    """Локальный черновик в `scripts/` — НЕ красное, иначе сторож бесполезен.

    В живом дереве таких сегодня двенадцать (`scripts/_*.py`,
    `scripts/debug_*.py`). Сторож, красный всегда, живёт до первого мержа.

    Маска здесь ПРИВЯЗАНА к каталогу — в этом и разница с
    `test_unanchored_mask_outside_packages_is_hard`, где всё то же самое, но
    маска голая.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "scripts/_*.py\n")
    _write(root, "scripts/_scratch.py", "A = 1\n")

    hard, soft = classify_invisible_sources(root, env)
    assert hard == []
    assert [rel for rel, _ in soft] == ["scripts/_scratch.py"]


def test_referenced_scratch_outside_packages_is_hard(tmp_path):
    """...но если на «черновик» уже ссылается видимый код — это красное.

    Признак взят из МЕХАНИЗМА (на файл опираются), а не из списка имён.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "scripts/_*.py\n")
    _write(root, "scripts/_helper.py", "A = 1\n")
    _write(root, "scripts/run.ps1", 'python "scripts/_helper.py" --once\n')

    hard, soft = classify_invisible_sources(root, env)
    assert [rel for rel, _, _ in hard] == ["scripts/_helper.py"], hard
    assert soft == []
    assert "scripts/run.ps1" in hard[0][2]


def test_missing_git_is_a_skip_not_a_pass(tmp_path, monkeypatch):
    """git недоступен → skip с причиной, а не зелёное.

    «Не смогли проверить» и «проверили, всё чисто» — разные вещи.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # ВНИМАНИЕ: `pytest.skip` бросает наследника BaseException, а не Exception —
    # `pytest.raises(Exception)` его НЕ ловит, и этот тест сам молча пропускался
    # бы, ничего не доказывая. Ловим ровно тот класс, что бросает skip.
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_git(tmp_path)
    assert "git" in str(excinfo.value)


def test_non_repo_directory_is_a_skip(tmp_path):
    """Каталог не репозиторий → skip с причиной."""
    if shutil.which("git") is None:
        pytest.skip("git не найден в PATH")
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    env = dict(os.environ)
    # Иначе git поднялся бы вверх и нашёл чужой репозиторий над tmp_path.
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "no-global-config")
    env["GIT_CONFIG_SYSTEM"] = str(tmp_path / "no-system-config")
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_git(plain, env)
    assert "репозитор" in str(excinfo.value)


# ---------------------------------------------------------------------------
# сторож 2: BOM — известное состояние, которое не должно РАСТИ
# ---------------------------------------------------------------------------


def test_bom_py_count_does_not_grow():
    """Число `.py` с UTF-8 BOM не растёт (сегодня 137).

    ПОЧЕМУ ЭТО ВАЖНО. `open(path, encoding="utf-8")` НЕ съедает BOM: первым
    символом приходит `\\ufeff`, и `ast.parse` на нём падает с SyntaxError.
    Любой статический сторож, который такую ошибку проглотит (`try/except
    SyntaxError: continue`), становится СЛЕП ровно на этих файлах — оставаясь
    ЗЕЛЁНЫМ. Слепота растёт вместе с числом BOM-файлов, поэтому число
    зафиксировано ЛИТЕРАЛОМ и замерено руками, а не выведено из кода:
    выведенное число согласно с деревом по определению и не краснеет никогда.

    Порог — «не больше сегодняшнего», а не «ровно столько»: убавить BOM
    хорошо, и краснеть на этом нельзя.

    Починка для нового файла — сохранить `.py` без BOM (UTF-8, LF). Для `.ps1`
    всё наоборот: там BOM ОБЯЗАТЕЛЕН, без него PS 5.1 читает как cp1251.
    Починка для сторожа — читать `encoding="utf-8-sig"`.

    Чинить 137 существующих файлов этот тест НЕ требует.
    """
    root = repo_root()
    require_git(root)
    found = bom_py_files(root)
    assert len(found) <= BOM_PY_FILE_CAP, (
        f"файлов .py с UTF-8 BOM стало {len(found)} при потолке {BOM_PY_FILE_CAP}.\n"
        "BOM — ловушка для статических сторожей: open(..., encoding='utf-8') отдаёт\n"
        "первым символом \\ufeff, ast.parse падает SyntaxError, и сторож, который\n"
        "эту ошибку глотает, слепнет на файле, ОСТАВАЯСЬ ЗЕЛЁНЫМ.\n"
        "Сохрани новый файл без BOM (UTF-8, LF) — или, если BOM осознан,\n"
        f"подними {BOM_PY_FILE_CAP} и объясни в коммите, чем это оправдано.\n"
        "Все файлы с BOM сейчас:\n  " + "\n  ".join(found)
    )


def test_bom_detection_actually_detects(tmp_path):
    """Сам детектор BOM не слеп: подставное дерево с BOM-файлом и без него.

    Без этого «137 <= 137» было бы зелено и при насмерть сломанном детекторе.
    """
    root, env = _init_fake_repo(tmp_path)
    _write(root, ".gitignore", "scripts/_*.py\n")
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "with_bom.py").write_bytes(b"\xef\xbb\xbfA = 1\n")
    (root / "app" / "no_bom.py").write_bytes(b"A = 1\n")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    # Проигнорированный черновик с BOM в счёт попадать НЕ должен: иначе число
    # прыгает от машины к машине и потолок краснеет от чужого мусора.
    (root / "scripts" / "_scratch.py").write_bytes(b"\xef\xbb\xbfB = 2\n")

    assert bom_py_files(root, env) == ["app/with_bom.py"]


def test_bom_makes_utf8_ast_parse_blind(tmp_path):
    """Доказательство утверждения из докстроки, а не пересказ его словами."""
    source = tmp_path / "bom_module.py"
    source.write_bytes(b"\xef\xbb\xbfVALUE = 1\n")

    with pytest.raises(SyntaxError):
        ast.parse(source.read_text(encoding="utf-8"))

    # ...а с utf-8-sig разбирается нормально — вот и починка для сторожей.
    assert ast.parse(source.read_text(encoding="utf-8-sig")) is not None
