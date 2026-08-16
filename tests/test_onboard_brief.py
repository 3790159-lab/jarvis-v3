# -*- coding: utf-8 -*-
"""T1 арки `chatter.onboard`: xlsx → brief.json (спека §1, план «Контракт данных»).

Сторожа написаны ОТ СПЕКИ. Реализация (`chatter/onboard/brief.py`,
`form_schema.yaml`) при написании НЕ читалась — иначе тест и код наследуют одно
допущение и оба зеленеют на неверном поведении.

Что защищается и чего стоит ошибка:

  1. **Отпечаток вопроса.** Ведёт позиция колонки, сторожит отпечаток. В живом
     брифе одновременно живут апострофы `U+02BC` («імʼя») и `U+2019` — сравнение
     по сырому тексту здесь ломается на невидимом символе.
  2. **Расхождение схемы и брифа — ГРОМКОЕ.** Тихий фолбэк здесь означает прайс
     клиента, собранный из чужой колонки (DEV-18: не глотать).
  3. **Мусор-детектор ничего не удаляет.** Он понижает поле до «ответа нет», но
     улику (`raw`) сохраняет; `value` при этом обязан быть `None`, а `reason`
     непуст — иначе мусор доедет до конфига, а баг детектора будет неотличим от
     честного мусора.
  4. **Ложные срабатывания дороже пропусков.** Детектор, срезавший живой ответ
     клиента, стоит потерянного поля — и это никто не заметит.
  5. **Маршрут объявлен в схеме, а не выведен из текста** (§1.3, C9): ICP в
     knowledge однажды будет зачитан лиду.

Сети нет, настоящий бриф клиента (имя, контакты, внутренние цены) не читается —
все фикстуры собираются здесь же через `openpyxl`.
"""
from __future__ import annotations

import openpyxl
import pytest
import yaml

from chatter.onboard import brief

# ── символы, на которых ломается сравнение по сырому тексту ────────────────
NBSP = " "
APO_MOD = "ʼ"  # ʼ MODIFIER LETTER APOSTROPHE — живёт в «імʼя» брифа Ярины
APO_RSQ = "’"  # ’ RIGHT SINGLE QUOTATION MARK — живёт там же
APO_ASC = "'"       # ' обычный

THRESHOLD = 0.6  # §1.1, порог Jaccard

# ── вопросы фикстуры (форма клиента, но выдуманная) ────────────────────────
Q_TIME = "Отметка времени"
Q_NAME = f"Як звати власника (ім{APO_MOD}я та прізвище)?"
Q_CITY = "У якому місті працює студія?"
Q_PRICE = "Для КОЖНОЇ послуги: ціна або вилка"
Q_ICP = "Хто ваш ідеальний клієнт?"


# ── сборка фикстур ─────────────────────────────────────────────────────────

def entry(col, question, *, fid, target="knowledge", type_="text", required=True):
    """Запись `form_schema.yaml` в формате §1.1.

    `fingerprint` кладём ТОКЕНАМИ из самого же `normalize_question` — так тест
    не зависит от конкретного стоп-словаря реализации, но проверяет контракт
    (порог, громкий отказ). `question` кладём тоже: карта расхождения обязана
    показать человеку ОБА текста, а из одних токенов «ожидали ~«…»» не собрать.
    """
    return {
        "id": fid,
        "col": col,
        "question": question,
        "fingerprint": sorted(brief.normalize_question(question)),
        "type": type_,
        "required": required,
        "target": target,
    }


def load_schema(tmp_path, entries, *, version=1):
    """Пишем `form_schema.yaml` и читаем его ОБЪЯВЛЕННЫМ загрузчиком.

    Схему в память руками не собираем: тогда тест зафиксировал бы внутреннюю
    форму dict'а, а не формат файла, который правит человек.
    """
    doc = {
        # Спека НЕ называет ключ версии в самом form_schema.yaml (она называет
        # `schema_version` только в brief.json и в отчёте). Пишем оба, чтобы
        # спор об имени ключа не глушил 22 сторожа поведения; само требование
        # «версия доезжает до артефакта» стоит отдельным тестом ниже.
        "schema_version": version,
        "version": version,
        "fields": [dict(e) for e in entries],
    }
    path = tmp_path / "form_schema.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return brief.load_schema(path)


def write_xlsx(tmp_path, headers, *answer_rows, name="brief.xlsx"):
    """Один лист: строка 0 — вопросы, строка 1 и дальше — заявки (§1)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ответы на форму (1)"
    ws.append(list(headers))
    for row in answer_rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    return path


MINI = [
    entry(0, Q_TIME, fid="q0_timestamp", target="report_only"),
    entry(1, Q_NAME, fid="q1_owner_name", target="settings"),
    entry(2, Q_CITY, fid="q2_city", target="knowledge"),
    entry(3, Q_PRICE, fid="q3_price_list", target="knowledge", type_="block"),
    entry(4, Q_ICP, fid="q4_icp", target="playbook"),
]
MINI_HEADERS = [Q_TIME, Q_NAME, Q_CITY, Q_PRICE, Q_ICP]
MINI_ANSWERS = [
    "16.08.2026 12:00:01",
    "Ярина Волошин",
    "Київ",
    "Полірування кузова — ціна 5 000–8 000 грн, тривалість 3 години",
    "Власник авто преміум-класу, готовий інвестувати в догляд",
]


def mini(tmp_path, answers=None, *, row=1):
    schema = load_schema(tmp_path, MINI)
    xlsx = write_xlsx(tmp_path, MINI_HEADERS, list(answers or MINI_ANSWERS))
    return brief.parse_brief(xlsx, schema, row=row)


# наполнитель, чтобы номер колонки в карте расхождения был различимым (22),
# а не «двойкой», которая случайно найдётся в любом сообщении
FILLER = ["послуги", "майстер", "студія", "обладнання", "матеріали", "графік",
          "оплата", "гарантія", "знижка", "доставка", "парковка", "запис",
          "реклама", "конкуренти", "відгуки", "фото", "склад", "персонал",
          "навчання", "сезон", "документи"]


def wide_entries(price_question=Q_PRICE):
    es = [entry(0, Q_TIME, fid="q0_timestamp", target="report_only")]
    for i, w in enumerate(FILLER, start=1):
        es.append(entry(i, f"Розкажіть про {w}", fid=f"q{i}_{i}",
                        target="report_only"))
    es.append(entry(22, price_question, fid="q22_price_list", type_="block"))
    return es


def wide_headers():
    return [e["question"] for e in wide_entries()]


def shown(msg, text):
    """Текст «ожидали» опознаваем: либо дословно, либо всеми своими токенами.

    Спека печатает «ожидали ~«Для КОЖНОЇ послуги…»», но в §1.1 у записи схемы
    лежит только `fingerprint`. Обе формы человек читает одинаково; чего быть
    НЕ должно — это «схема не совпала» без указания, чего ждали.
    """
    if text in msg:
        return True
    tokens = brief.normalize_question(text)
    return bool(tokens) and all(t in msg.casefold() for t in tokens)


# ══ 1. ОТПЕЧАТОК ВОПРОСА (§1.1) ════════════════════════════════════════════

def test_all_apostrophe_variants_give_one_and_the_same_fingerprint():
    """Класс ошибки: колонка «не совпала» из-за невидимого символа.

    В брифе Ярины `U+02BC` и `U+2019` живут ОДНОВРЕМЕННО. Если апострофы не
    сведены к одному виду, «імʼя» из схемы и «ім’я» из формы — разные токены,
    отпечаток рушится, и пайплайн упирается в rc 2 на честном брифе.
    """
    variants = [f"Як звати власника (ім{a}я та прізвище)?"
                for a in (APO_MOD, APO_RSQ, APO_ASC)]
    fps = [brief.normalize_question(v) for v in variants]
    assert fps[0] == fps[1] == fps[2], f"апострофы разошлись: {fps}"
    assert fps[0], "отпечаток не может быть пустым на осмысленном вопросе"


def test_case_and_punctuation_do_not_move_the_fingerprint():
    """Класс ошибки: правка регистра или знака в форме = «схема не совпала».

    Формулировки правят чаще, чем переставляют вопросы (§1.1); отпечаток обязан
    переживать косметику, иначе он не сторож, а генератор ложных красных.
    """
    assert (brief.normalize_question("Для КОЖНОЇ послуги: ціна або вилка")
            == brief.normalize_question("для кожної послуги — ціна, або вилка!"))


def test_nbsp_is_a_separator_not_a_letter():
    """Класс ошибки: из Google Forms NBSP приезжает регулярно.

    Без замены NBSP→пробел «ціна\\u00a0послуги» — ОДИН токен, и он не совпадёт
    ни с чем. Та же грабля отдельно живёт в R1/C8 на стороне генератора.
    """
    assert (brief.normalize_question(f"Ціна{NBSP}послуги")
            == brief.normalize_question("Ціна послуги"))
    assert all(NBSP not in t for t in brief.normalize_question(f"а{NBSP}б"))


def test_pure_function_words_are_dropped():
    """Класс ошибки: служебные слова раздувают объединение и топят Jaccard.

    Спека требует «выкинуть стоп-слова», но списка не даёт. Фиксирую минимум по
    её же примеру вопроса: «для» и «або» — чисто служебные, смысла колонки не
    несут; если они остаются токенами, две переформулировки одного вопроса
    сравниваются по мусору.
    """
    tokens = brief.normalize_question("Для КОЖНОЇ послуги: ціна або вилка")
    assert "для" not in tokens
    assert "або" not in tokens
    assert {"ціна", "вилка"} <= tokens, "смысловые токены выкидывать нельзя"


def test_fingerprint_is_a_set_of_clean_tokens():
    """Класс ошибки: повтор слова в вопросе даёт вес, которого нет в смысле.

    Множество (а не список) — часть контракта: Jaccard на списках с дублями
    считается иначе и порог 0.6 начинает означать другое.
    """
    tokens = brief.normalize_question("Ціна, ціна та ще раз ЦІНА?")
    assert isinstance(tokens, set)
    assert all(t == t.casefold() and t.strip() == t and " " not in t
               for t in tokens), tokens


def test_a_blank_question_has_an_empty_fingerprint():
    """Класс ошибки: пустая колонка «совпадает» со всем подряд.

    Пустой заголовок обязан дать пустой отпечаток, а не токен из пробела —
    иначе он получает ненулевое сходство и молча занимает чужую позицию.
    """
    for blank in ("", "   ", NBSP, "—", "..."):
        assert brief.normalize_question(blank) == set(), repr(blank)


# ══ 2. СХОДСТВО ОТПЕЧАТКОВ ═════════════════════════════════════════════════

def test_identical_fingerprints_score_one():
    fp = brief.normalize_question(Q_PRICE)
    assert brief.fingerprint_score(fp, fp) == pytest.approx(1.0)


def test_disjoint_fingerprints_score_zero():
    """Класс ошибки: любая пара вопросов «немного похожа» и порог не работает."""
    a = brief.normalize_question("Ціни на послуги")
    b = brief.normalize_question("Хто ваш ідеальний клієнт?")
    assert brief.fingerprint_score(a, b) == pytest.approx(0.0)


def test_score_is_jaccard_not_containment():
    """Класс ошибки: подсчёт «доли общих от меньшего» вместо Jaccard.

    Тогда короткий вопрос-огрызок совпадает с длинным на 1.0, и колонка
    подменяется молча. {a,b,c} против {b,c,d}: общих 2, объединение 4 → 0.5.
    """
    assert brief.fingerprint_score({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)
    assert brief.fingerprint_score({"a", "b"}, {"a", "b", "c", "d"}) == pytest.approx(0.5)


def test_score_is_symmetric():
    a, b = {"ціна", "вилка", "послуги"}, {"ціна", "послуги"}
    assert brief.fingerprint_score(a, b) == brief.fingerprint_score(b, a)


def test_empty_fingerprints_never_count_as_a_match():
    """Класс ошибки: 0/0 прочитано как «совпало на 1.0».

    Конкретно: в `form_schema.yaml` забыли заполнить `fingerprint`, а заголовок
    колонки в форме состоит из одних служебных слов (или пуст). Оба отпечатка
    пусты — и единственный сторож позиции молча выдаёт максимальное сходство на
    паре, о которой не знает НИЧЕГО. Jaccard на двух пустых не определён;
    безопасная сторона одна — «доказательств совпадения нет».
    """
    assert brief.fingerprint_score(set(), set()) < THRESHOLD
    assert brief.fingerprint_score(set(), {"ціна"}) == pytest.approx(0.0)


# ══ 3. РАСХОЖДЕНИЕ СХЕМЫ И БРИФА = ГРОМКИЙ ОТКАЗ С КАРТОЙ (§1.3) ═══════════

def test_matching_brief_reports_nothing(tmp_path):
    """Базовая линия: на совпавшем брифе карта расхождений ПУСТА.

    Без этого сторожа «всегда красный» прошёл бы за «строгий».
    """
    schema = load_schema(tmp_path, MINI)
    assert brief.match_schema(MINI_HEADERS, schema) == []


def test_rewording_above_the_threshold_still_matches(tmp_path):
    """Класс ошибки: сторож громче ведущего.

    Позиция ВЕДЁТ, отпечаток СТОРОЖИТ. Правка формулировки, сохранившая
    сходство ≥ 0.6, обязана пройти молча — иначе каждая косметическая правка
    формы останавливает онбординг.
    """
    reworded = "Для кожної послуги — ціна"
    assert brief.fingerprint_score(brief.normalize_question(Q_PRICE),
                                   brief.normalize_question(reworded)) >= THRESHOLD
    schema = load_schema(tmp_path, MINI)
    headers = list(MINI_HEADERS)
    headers[3] = reworded
    assert brief.match_schema(headers, schema) == []


def test_rewording_below_the_threshold_is_reported_with_both_texts(tmp_path):
    """Класс ошибки: тихая догадка = прайс клиента из чужой колонки.

    Карта обязана назвать НОМЕР КОЛОНКИ и ОБА текста: без «ожидали» человек не
    знает, что чинить, без «нашли» — куда смотреть в форме.
    """
    found = "Ціни по послугах"
    assert brief.fingerprint_score(brief.normalize_question(Q_PRICE),
                                   brief.normalize_question(found)) < THRESHOLD
    schema = load_schema(tmp_path, wide_entries())
    headers = wide_headers()
    headers[22] = found
    report = brief.match_schema(headers, schema)
    assert report, "переформулированный ниже порога вопрос обязан быть замечен"
    msg = "\n".join(report)
    assert "22" in msg, f"нет номера колонки: {msg}"
    assert found in msg, f"нет текста «нашли»: {msg}"
    assert shown(msg, Q_PRICE), f"нет текста «ожидали»: {msg}"


def test_extra_column_in_the_brief_is_reported(tmp_path):
    """Класс ошибки: форму дополнили, схему забыли (§7, дрейф схемы).

    Лишняя колонка тихо проигнорирована = ответ клиента, которого никто не
    увидит. Обязана быть строка с её номером и текстом.
    """
    schema = load_schema(tmp_path, wide_entries())
    headers = wide_headers() + ["Чи потрібен нам виїзний детейлінг?"]
    report = brief.match_schema(headers, schema)
    assert report, "лишняя колонка обязана быть названа"
    msg = "\n".join(report)
    assert "23" in msg, f"нет номера лишней колонки: {msg}"
    assert "Чи потрібен нам виїзний детейлінг?" in msg, msg


def test_missing_column_in_the_brief_is_reported(tmp_path):
    """Класс ошибки: колонку из формы убрали, схема ждёт её на месте.

    Молча пропустить = собрать клиента без прайса и не сказать об этом.
    """
    schema = load_schema(tmp_path, wide_entries())
    headers = wide_headers()[:-1]  # колонки 22 больше нет
    report = brief.match_schema(headers, schema)
    assert report, "отсутствующая колонка обязана быть названа"
    msg = "\n".join(report)
    assert "22" in msg, f"нет номера отсутствующей колонки: {msg}"
    assert shown(msg, Q_PRICE), f"нет текста «ожидали»: {msg}"


def test_every_broken_column_is_named_not_just_the_first(tmp_path):
    """Класс ошибки: карта на одну строку = человек чинит форму по одной
    колонке за прогон. Спека печатает КАРТУ (в её примере сразу две строки)."""
    schema = load_schema(tmp_path, wide_entries())
    headers = wide_headers()
    headers[22] = "Ціни по послугах"
    headers[5] = "Зовсім інше питання про космос"
    msg = "\n".join(brief.match_schema(headers, schema))
    assert "22" in msg and "5" in msg, f"названа не каждая колонка: {msg}"


def test_parse_brief_refuses_a_brief_that_does_not_match_the_schema(tmp_path):
    """Класс ошибки: расхождение замечено, но разбор всё равно состоялся.

    Тихий фолбэк здесь = прайс из чужой колонки, доехавший до конфига клиента.
    Отказ обязан быть исключением, и в тексте обязана быть та же карта.
    """
    schema = load_schema(tmp_path, MINI)
    headers = list(MINI_HEADERS)
    headers[3] = "Ціни по послугах"
    xlsx = write_xlsx(tmp_path, headers, MINI_ANSWERS)
    with pytest.raises(brief.SchemaMismatch) as exc:
        brief.parse_brief(xlsx, schema)
    msg = str(exc.value)
    assert "3" in msg, f"нет номера колонки: {msg}"
    assert "Ціни по послугах" in msg, f"нет текста «нашли»: {msg}"
    assert shown(msg, Q_PRICE), f"нет текста «ожидали»: {msg}"


# ══ 4. МУСОР-ДЕТЕКТОР: ШЕСТЬ КЛАССОВ (§1.2) ════════════════════════════════
#
# Все примеры взяты из таблицы спеки, то есть из РЕАЛЬНОГО брифа Ярины.
# Проверяем «не ok», а не конкретный ярлык: спека не говорит, какой класс
# suspect, а какой garbage. Общее у всех одно — в конфиг они не попадают.

VERDICTS = {"ok", "suspect", "garbage"}


def reject(raw, question):
    verdict, reason = brief.classify_value(raw, brief.normalize_question(question))
    assert verdict in VERDICTS, verdict
    assert verdict != "ok", f"мусор принят за ответ: {raw!r} → {verdict}/{reason}"
    assert reason, f"вердикт без причины неотличим от бага детектора: {raw!r}"
    return verdict, reason


@pytest.mark.parametrize("raw", ["тест 01 Артем", "тест", "test", "asdf", "---"])
def test_test_filler_is_not_an_answer(raw):
    """Класс ошибки: клиент проверял форму, а мы собрали из этого конфиг.

    Q5 «тест 01 Артем» и Q8 «тест» — из живого брифа; «Артем» уехал бы в имя
    владельца, и бот обращался бы к лиду от чужого имени.
    """
    reject(raw, Q_NAME)


@pytest.mark.parametrize("raw", ["1 1 1", "1.0", "1", "0", "1 1 1 1"])
def test_numeric_placeholder_in_a_text_field_is_not_an_answer(raw):
    """Класс ошибки: числовая заглушка в текстовом поле (Q10, Q52/Q54/Q55).

    «1 1 1» на вопрос «кто отвечает сейчас» — не ответ, а щелчок по клавише.
    Отдельно: «1.0» — пример СПЕКИ, но её же буквальное правило (доля цифр и
    пробелов > 0.8) на нём не срабатывает, точка в долю не входит. Требование
    фиксирую по примеру, а не по формуле.
    """
    reject(raw, "Скільки майстрів працює у студії зараз?")


@pytest.mark.parametrize("raw", ["мне)", "ну)", "-", "…"])
def test_a_stub_answer_is_not_an_answer(raw):
    """Класс ошибки: огрызок (Q36 «мне)») уезжает как контакт для уведомлений.

    Короткий обрывок без единого токена вопроса не отвечает ни на что; в конфиг
    он попал бы как факт, и бот сообщал бы «мне)».
    """
    reject(raw, "Кому надсилати сповіщення про нові заявки?")


@pytest.mark.parametrize("raw", ["заполнил разом", "см. выше", "там же", "дивись вище"])
def test_an_answer_pointing_at_another_field_is_not_an_answer(raw):
    """Класс ошибки: ссылка на соседнее поле (Q23, Q25) принята как содержание.

    В конфиг уехала бы строка «заполнил разом» — она не ложь, она пустота,
    которую никто не заметит, пока бот не зачитает её лиду.
    """
    reject(raw, "Які послуги ви НЕ надаєте?")


@pytest.mark.parametrize("raw", [
    "drivepro-detailing.example",
    "https://example.com",
    "test.com",
    "http://localhost:8000",
])
def test_a_placeholder_url_is_not_an_answer(raw):
    """Класс ошибки: плейсхолдер-домен (Q4) уходит лиду как сайт студии.

    Живая цена: лид получает ссылку, которая никуда не ведёт, и это выглядит
    как обман. Спека прямо требует заглушку вместо такого «сайта» (R7).
    """
    reject(raw, "Сайт, соцмережі, портфоліо")


@pytest.mark.parametrize("raw", ["Джарвис", "джарвис", "Jarvis"])
def test_our_own_name_in_a_client_field_is_not_an_answer(raw):
    """Класс ошибки: наше имя (Q14) становится именем персоны клиента.

    Это же детектор протечки чужого конфига — тот, что в R6 ловит имена персон
    других клиентов. Регистр значения не имеет: «джарвис» — то же имя.
    """
    reject(raw, "Як звати вашого адміністратора у переписці?")


# ── ЛОЖНЫЕ СРАБАТЫВАНИЯ: живой ответ обязан выжить ────────────────────────
#
# Детектор, режущий настоящие ответы, дороже детектора, который что-то
# пропустил: пропуск виден в разделе 3 отчёта, а потерянное поле — нигде.

def ok(raw, question):
    verdict, reason = brief.classify_value(raw, brief.normalize_question(question))
    assert verdict == "ok", f"живой ответ срезан: {raw!r} → {verdict}/{reason}"
    return verdict, reason


def test_a_real_price_answer_survives():
    """Класс ошибки: прайс из цифр и валюты сочтён числовой заглушкой.

    Это ровно то поле, ради которого арка существует; потеряв его, пайплайн
    соберёт клиента без цен и покажет зелёное.
    """
    ok("Полірування кузова — ціна 5 000–8 000 грн, тривалість 3 години", Q_PRICE)
    ok("Хімчистка салону 2 500 грн, детейлінг від 12 000 грн", Q_PRICE)


def test_a_short_but_meaningful_answer_survives():
    """Класс ошибки: правило огрызка («длина < 12 и ни одного токена вопроса»)
    режет честное «Київ» — в вопросе «У якому місті працює студія?» слова
    «Київ» нет и быть не может.

    Цена — потерянный город клиента. Требование: короткий осмысленный ответ
    остаётся `ok`; значит, одной длины и пересечения токенов правилу мало.
    """
    ok("Київ", Q_CITY)


def test_a_schedule_answer_with_numbers_survives():
    """Класс ошибки: график работы («з 9:00 до 20:00») принят за числовой
    мусор. Часы — источник факта «выходные дни» (R7); срезав их, пайплайн
    поставит заглушку там, где данные были."""
    ok("з 9:00 до 20:00, без вихідних", "Графік роботи студії")


def test_ok_verdict_carries_no_reason():
    """Класс ошибки: `reason` заполняется всегда, и «мусор без причины»
    перестаёт быть отличимым признаком (контракт: reason = null для ok)."""
    verdict, reason = brief.classify_value(
        "Полірування кузова — ціна 5 000–8 000 грн", brief.normalize_question(Q_PRICE))
    assert verdict == "ok"
    assert reason is None, f"у чистого поля не может быть причины: {reason!r}"


# ══ 5. ТРИ ИНВАРИАНТА ЗАПИСИ ПОЛЯ (план, «Контракт данных») ════════════════

DIRTY_QUESTIONS = [
    Q_TIME,
    Q_NAME,
    "Скільки майстрів працює у студії зараз?",
    "Кому надсилати сповіщення про нові заявки?",
    "Які послуги ви НЕ надаєте?",
    "Сайт, соцмережі, портфоліо",
    "Як звати вашого адміністратора у переписці?",
    Q_PRICE,
]
DIRTY_ANSWERS = [
    "16.08.2026 12:00:01",
    "тест 01 Артем",
    "1 1 1",
    "мне)",
    "заполнил разом",
    "drivepro-detailing.example",
    "Джарвис",
    "Полірування кузова — ціна 5 000–8 000 грн, тривалість 3 години",
]
DIRTY_IDS = ["q0_timestamp", "q1_owner_name", "q2_staff", "q3_notify",
             "q4_not_provided", "q5_links", "q6_admin_name", "q7_price_list"]


def dirty(tmp_path):
    entries = [entry(i, q, fid=DIRTY_IDS[i], target="report_only" if i == 0 else "knowledge")
               for i, q in enumerate(DIRTY_QUESTIONS)]
    schema = load_schema(tmp_path, entries)
    xlsx = write_xlsx(tmp_path, DIRTY_QUESTIONS, DIRTY_ANSWERS)
    return brief.parse_brief(xlsx, schema)


def test_raw_survives_every_verdict_including_garbage(tmp_path):
    """Класс ошибки: детектор УДАЛЯЕТ то, что забраковал.

    Тогда в разделе 3 отчёта нечего процитировать, и владелец не может решить,
    мусор это или странно записанная правда. Спека: «детектор ничего не удаляет
    — он понижает поле до „ответа нет“».
    """
    doc = dirty(tmp_path)
    for fid, expected in zip(DIRTY_IDS, DIRTY_ANSWERS):
        rec = doc["fields"][fid]
        assert rec["raw"] == expected, f"{fid}: улика потеряна ({rec['raw']!r})"


def test_value_is_none_for_every_non_ok_verdict(tmp_path):
    """Класс ошибки: мусор доезжает до конфига.

    Ровно это правило — единственное, что стоит между «1 1 1» и knowledge.md
    живого клиента.
    """
    doc = dirty(tmp_path)
    for fid, rec in doc["fields"].items():
        assert rec["verdict"] in VERDICTS, (fid, rec["verdict"])
        if rec["verdict"] != "ok":
            assert rec["value"] is None, f"{fid}: мусор с значением {rec['value']!r}"


def test_reason_is_never_empty_for_a_non_ok_verdict(tmp_path):
    """Класс ошибки: поле забраковано без причины.

    «Мусор без причины» неотличим от бага детектора — ни отчёт, ни человек не
    могут сказать, поле пустое или детектор сломался.
    """
    doc = dirty(tmp_path)
    for fid, rec in doc["fields"].items():
        if rec["verdict"] != "ok":
            assert rec["reason"], f"{fid}: вердикт {rec['verdict']} без причины"


def test_ok_fields_carry_a_value_and_no_reason(tmp_path):
    """Обратная сторона тех же двух инвариантов: `ok` без значения — это поле,
    которое пайплайн считает заполненным, а генератор получит пустоту."""
    doc = dirty(tmp_path)
    for fid, rec in doc["fields"].items():
        if rec["verdict"] == "ok":
            assert rec["value"] is not None, f"{fid}: ok без значения"
            assert rec["reason"] is None, f"{fid}: ok с причиной {rec['reason']!r}"


def test_at_least_one_garbage_and_one_ok_field_are_present(tmp_path):
    """Сторож на сторожей: если бы всё оказалось `ok` (или всё — мусором), три
    инварианта выше прошли бы вхолостую и ничего не доказали."""
    doc = dirty(tmp_path)
    verdicts = {rec["verdict"] for rec in doc["fields"].values()}
    assert "ok" in verdicts, "ни одного чистого поля — инварианты не проверены"
    assert verdicts - {"ok"}, "ни одного забракованного поля — детектор молчал"


# ══ 6. МАРШРУТ ОБЪЯВЛЕН В СХЕМЕ (§1.3, C9) ═════════════════════════════════

def test_playbook_target_is_taken_from_the_schema_not_from_the_text(tmp_path):
    """Класс ошибки: маршрут выведен из содержания.

    «Наш ідеальний клієнт — той, хто готовий інвестувати» выглядит как обычный
    текст о компании; попав в knowledge, она однажды будет ЗАЧИТАНА лиду.
    Спека: правило по ИСТОЧНИКУ, распознавать «внутреннее» по содержанию мы не
    пытаемся и не будем.
    """
    doc = mini(tmp_path)
    assert doc["fields"]["q4_icp"]["target"] == "playbook"
    assert doc["fields"]["q3_price_list"]["target"] == "knowledge"
    assert doc["fields"]["q0_timestamp"]["target"] == "report_only"
    assert doc["fields"]["q1_owner_name"]["target"] == "settings"


def test_target_survives_a_garbage_verdict(tmp_path):
    """Класс ошибки: у забракованного поля маршрут «на всякий случай» сбросили.

    Отчёту (раздел 3) маршрут нужен и у мусора: он говорит, ЧТО именно осталось
    незакрытым — внутренняя разметка или то, из чего бот говорит лиду.
    """
    answers = list(MINI_ANSWERS)
    answers[4] = "тест"
    doc = mini(tmp_path, answers)
    rec = doc["fields"]["q4_icp"]
    assert rec["verdict"] != "ok"
    assert rec["target"] == "playbook", "маршрут потерян вместе с содержанием"


# ══ 7. ФОРМА ВЫХОДА (план, «Контракт данных») ══════════════════════════════

FIELD_KEYS = {"col", "question", "raw", "value", "verdict", "reason", "target"}


def test_field_record_has_exactly_the_contract_keys(tmp_path):
    """Класс ошибки: T2/T3/T4 пишутся параллельно от ЭТОГО контракта.

    Лишний ключ — приглашение опереться на то, чего в контракте нет; недостающий
    — падение второй волны на приёмке, когда чинить дороже всего.
    """
    doc = mini(tmp_path)
    for fid, rec in doc["fields"].items():
        assert set(rec) == FIELD_KEYS, f"{fid}: {sorted(set(rec) ^ FIELD_KEYS)}"


def test_document_carries_schema_version_and_fields(tmp_path):
    """Класс ошибки: версия схемы потеряна.

    Форму правят, схему забывают (§7); без `schema_version` в артефакте нельзя
    сказать, какой версией собран конфиг клиента.
    """
    doc = mini(tmp_path)
    assert doc["schema_version"] == 1
    assert set(doc["fields"]) == set(e["id"] for e in MINI)


def test_question_is_the_header_text_verbatim(tmp_path):
    """Класс ошибки: в артефакт уехал нормализованный вопрос.

    Отчёт цитирует вопрос человеку (раздел 1); нормализованный текст без
    апострофов и регистра клиент в своей форме не узнает.
    """
    doc = mini(tmp_path)
    assert doc["fields"]["q1_owner_name"]["question"] == Q_NAME
    assert APO_MOD in doc["fields"]["q1_owner_name"]["question"]


def test_col_is_the_column_declared_in_the_schema(tmp_path):
    """Класс ошибки: `col` пересчитан «от себя» и разошёлся с картой ошибок.

    Тогда сообщение «колонка 22» указывает не на ту колонку, и человек правит
    здоровое место.
    """
    doc = mini(tmp_path)
    assert [doc["fields"][e["id"]]["col"] for e in MINI] == [0, 1, 2, 3, 4]


def test_default_row_is_the_first_application(tmp_path):
    """Строка 0 — вопросы, строка 1 — ответы (§1). Сдвиг на единицу дал бы
    конфиг, собранный из текстов вопросов, и это выглядело бы правдоподобно."""
    doc = mini(tmp_path)
    assert doc["fields"]["q2_city"]["raw"] == "Київ"


def test_row_argument_selects_which_application_is_parsed(tmp_path):
    """Класс ошибки: «одна строка = одна заявка», но берётся всегда первая.

    Форма накапливает заявки; онбординг второго клиента из того же файла собрал
    бы конфиг ПЕРВОГО — с его ценами и его именем.
    """
    schema = load_schema(tmp_path, MINI)
    second = ["17.08.2026 09:00:00", "Оксана Ковальчук", "Львів",
              "Детейлінг — ціна 12 000–15 000 грн, тривалість 2 дні",
              "Власник авто преміум-класу"]
    xlsx = write_xlsx(tmp_path, MINI_HEADERS, MINI_ANSWERS, second)
    doc = brief.parse_brief(xlsx, schema, row=2)
    assert doc["fields"]["q2_city"]["raw"] == "Львів"
    assert doc["fields"]["q1_owner_name"]["raw"] == "Оксана Ковальчук"


def test_an_empty_cell_never_produces_a_value(tmp_path):
    """Класс ошибки: пустая ячейка приезжает как строка «None» или пустая
    строка-значение.

    Пустое поле — материал раздела 3 отчёта и заглушки (R7). Значение у него
    появиться не может ни при каком вердикте.
    """
    answers = list(MINI_ANSWERS)
    answers[2] = None
    doc = mini(tmp_path, answers)
    rec = doc["fields"]["q2_city"]
    assert rec["value"] is None, f"пустая ячейка дала значение {rec['value']!r}"
    assert rec["raw"] in (None, ""), f"выдуманная улика: {rec['raw']!r}"
