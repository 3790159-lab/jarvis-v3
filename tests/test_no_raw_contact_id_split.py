# -*- coding: utf-8 -*-
"""Сторожа 8 и 9 арки «личность контакта» (ВЕБ, волна 1, пара A) — по AST.

Писаны ОТ ТЕКСТА СПЕКИ и ДО кода. Спека §5 называет эти два сторожа по AST
прямо — это оговорённое исключение из домашнего правила «мерим ИСХОД, а не
текст»: утверждение «ручного разбора БОЛЬШЕ НЕ ОСТАЛОСЬ» — это утверждение о
дереве, и исходом его не измерить. Ни один прогон не докажет отсутствие
девятого места, которое просто не позвали.

Почему ОТДЕЛЬНЫЙ файл, а не вместе с остальными восемью сторожами. Соседний
`guards_a_contact_ref.py` импортирует `chatter.core.contact_ref` на уровне
модуля и до появления реализации падает на СБОРЕ — целиком. Лежи эти два
сторожа там же, их результат был бы съеден чужим ImportError, и мы бы не
узнали ни того, что ручной разбор ещё на месте (это ожидаемо красное), ни
того, что кто-то «заодно» причесал хвост (а это красное неожиданное).
Здесь импортов нового модуля НЕТ намеренно: файл работает и до, и после кода.

Сторож 9 — ВСТРЕЧНАЯ ПОЛОВИНА к сторожу 8. Восьмой требует «убрать `split`»,
и самая дешёвая правка под это требование — пройти регуляркой по всем
`contact_id.<что-нибудь>split` и заменить всё подряд. Тогда `rsplit` в
`_persona_settings` (сегодня `telethon_run.py:1397`) уедет вместе с ними —
а он ЦЕЛ и при удлинении головы работает как работал. Односторонний сторож
такую правку принял бы за успех.

Корень репозитория берём как `parent.parent` от файла, чтобы сторожа работали,
лёжа в `tests/` ЛЮБОГО worktree, а не только боевого дерева (DEV-78: приёмка
идёт в отдельном дереве, и путь, зашитый абсолютом, там слепнет).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = ("chatter", "app")

# Восемь мест из §1 замера. Литеральный список, а не выведенный обходом: список,
# выведенный из кода, согласен с кодом по определению и промолчит ровно там, где
# код забыл ([[jarvis-literal-lists-not-introspection]]). Ниже он служит не
# фильтром, а РАСШИФРОВКОЙ в сообщении об ошибке — чтобы читающий красное сразу
# видел, попал он в известное место или нашёл девятое, о котором замер не знал.
KNOWN_SITES = (
    "chatter/telethon_run.py",
    "chatter/run.py",
    "chatter/notify/control_bot.py",
    "app/services/tamapi_metrics.py",
)


def _python_files():
    """Все .py под chatter/ и app/. Fail-closed на пропавший корень: сканер,
    которому нечего сканировать, зелен по построению — а это ровно тот способ
    врать, ради которого сторож и написан."""
    found = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        assert base.is_dir(), (
            f"каталог {base} не найден — сканер AST не увидел бы НИЧЕГО и "
            f"позеленел бы впустую; проверь, что корень репозитория вычислен "
            f"верно (сейчас {REPO_ROOT})."
        )
        found.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    assert found, (
        f"под {SCAN_ROOTS} в {REPO_ROOT} не нашлось ни одного .py; пустой обход "
        f"даёт зелёный сторож при полностью не проверенном дереве."
    )
    return found


def _parse(path: Path) -> ast.AST:
    """Разбор с ГРОМКИМ отказом. Файл, который сканер не смог прочитать, — это
    файл, в котором он слеп; молча пропустить его значит отчитаться «чисто» о
    коде, который никто не смотрел.

    `utf-8-sig`, а не `utf-8`, и это НЕ придирка: 108 файлов под `app/` лежат
    сегодня с BOM. Сам Python их читает (токенизатор BOM снимает), а наивный
    `read_text(encoding='utf-8')` оставляет U+FEFF первым символом и получает
    SyntaxError на КАЖДОМ. Сторож бы покраснел стопкой ложных отказов, и
    настоящая находка утонула бы среди них — красное не о том вопросе хуже
    зелёного, потому что его перестают читать."""
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as e:
        raise AssertionError(
            f"{path.relative_to(REPO_ROOT).as_posix()} не разобрался AST ({e}); "
            f"сканер по этому файлу СЛЕП, и его молчание нельзя считать "
            f"подтверждением чистоты."
        ) from e


def _denotes_contact_id(node: ast.AST) -> bool:
    """Тот ли это объект, что мы ищем: сам `contact_id`, `row["contact_id"]`
    или `obj.contact_id`.

    Две формы реально живут в дереве сегодня — `contact_id.split(":", 1)` и
    `row["contact_id"].split(":")`, — и они РАЗНЫЕ. Правка, причесавшая только
    первую, оставила бы вторую (`telethon_run.render_status`) нетронутой, а
    именно она печатает владельцу /status. Поэтому узнаём контакт по имени, а
    не по одному написанию."""
    if isinstance(node, ast.Name):
        return node.id == "contact_id"
    if isinstance(node, ast.Attribute):
        return node.attr == "contact_id"
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == "contact_id"
    return False


def _raw_splits_of_contact_id(tree: ast.AST):
    """Вызовы `<contact_id>.split(...)` — с любым числом аргументов.

    `maxsplit` намеренно НЕ проверяем: `split(":")` и `split(":", 1)` — это
    два написания одного и того же ручного разбора, и оба из восьми мест.
    Сторож, требующий буквально `split(":", 1)`, пропустил бы
    `render_status`."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "split"
                and _denotes_contact_id(node.func.value)):
            yield node


def test_no_raw_contact_id_split_is_left_in_chatter_or_app():
    """Сторож 8: ручного разбора головы в `chatter/` и `app/` не осталось нигде.

    Без этого сторожа арка проверяет только то, что новая функция существует и
    правильно считает, — а восемь мест могут продолжать резать строку сами.
    Хуже: девятое место, добавленное завтра копипастой из восьмого, никакой
    юнит-тест не поймает вовсе. Дефект, который тут ловим, молчаливый: шесть из
    восьми мест на чужой форме возвращают правдоподобную строку `"instagram"`
    вместо собеседника, и она доезжает до клиента."""
    offenders = []
    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in _raw_splits_of_contact_id(_parse(path)):
            known = "известное место замера" if rel in KNOWN_SITES else "НОВОЕ место, замером не учтённое"
            offenders.append(f"{rel}:{node.lineno} ({known})")
    assert not offenders, (
        "ждали НОЛЬ ручных `contact_id.split(...)` в chatter/ и app/ — весь "
        "разбор головы обязан идти через chatter.core.contact_ref; нашли "
        f"{len(offenders)}:\n  " + "\n  ".join(offenders) +
        "\nКаждое такое место на форме, которой не знает, вернёт правдоподобный "
        "мусор вместо собеседника — молча, и мусор доедет до клиента."
    )


def test_the_slug_tail_rsplit_survived_the_cleanup():
    """Сторож 9, встречная половина к 8: хвост НЕ причесали заодно.

    `_persona_settings` берёт слуг персоны хвостом (`rsplit(":", 1)[-1]`) —
    и это правильно: при удлинении головы хвост работает как работал, чинить
    там нечего. Сторож 8 требует убрать `split`, и массовая правка «по всем
    вхождениям» снесла бы и `rsplit`. Что сломалось бы: слуг перестал бы
    находиться, `self.personas.get(slug, ...)` МОЛЧА свалился бы на primary,
    и карточка ушла бы владельцу на языке чужой персоны — без единого
    исключения в логе.

    Принимаем оба честных исхода: живой `rsplit` по contact_id, либо вызов
    `slug_of` (та же операция, вынесенная в модуль). Не принимаем — пропажу
    обоих и подмену на `split`. Ищем ПО ИМЕНИ ФУНКЦИИ, а не по номеру строки:
    номера уже уезжали между замерами 24.08 и 28.08."""
    path = REPO_ROOT / "chatter" / "telethon_run.py"
    assert path.is_file(), (
        f"{path} не найден — без него сторож не может ни подтвердить, ни "
        f"опровергнуть сохранность хвоста, и его зелёный цвет ничего не значит."
    )
    tree = _parse(path)
    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_persona_settings"),
        None,
    )
    assert target is not None, (
        "в chatter/telethon_run.py пропала функция `_persona_settings` — это "
        "единственное место, где из contact_id берётся слуг персоны; её "
        "исчезновение (или переименование) обязано быть замечено, а не "
        "проглочено сторожем как 'ну и ладно'."
    )

    has_rsplit = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "rsplit" and _denotes_contact_id(n.func.value)
        for n in ast.walk(target)
    )
    has_slug_of = any(
        isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == "slug_of")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "slug_of"))
        for n in ast.walk(target)
    )
    assert has_rsplit or has_slug_of, (
        "в `_persona_settings` (chatter/telethon_run.py) не осталось ни "
        "`contact_id.rsplit(...)`, ни вызова `slug_of(...)`: хвост причесали "
        "заодно с головой. Слуг персоны перестанет находиться, "
        "`personas.get(slug, primary)` МОЛЧА свалится на primary — карточка "
        "уйдёт владельцу на языке чужой персоны, и в логе не будет ничего."
    )
