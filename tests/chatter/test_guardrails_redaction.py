"""P20 (D): точечная редакция вместо подавления всего ответа.

Дефект (б) инцидента 2026-07-29: один незабэканный номер убивал ВЕСЬ ответ —
вместе с ним гибли 750–900 $ и 600–800 $, обеспеченные knowledge. Лид трижды
не увидел ни одной цены.

Контракт: редактируется КЛАУЗА целиком (грамматически цельный кусок между
запятыми/скобками/точками), а не число внутри неё — иначе в предложении
остаётся дыра. Соседние обеспеченные числа обязаны выжить.
"""
from __future__ import annotations

import re

import pytest

from chatter.core.guardrails import contains_unbacked_claim, redact_unbacked

KNOWLEDGE = (
    "# Ціни\n"
    "- SMM-ведення — 750–900 $\n"
    "- Маркетингова стратегія — 600–800 $\n"
    "# Терміни\n"
    "- Маркетингова стратегія — 21 календарний день\n"
    "- Створення логотипа — 5–10 робочих днів\n"
)


def test_redaction_replaces_only_the_offending_clause():
    reply = ("Маркетингова стратегія - 600–800 $, термін розробки 99 днів, "
             "потім презентуємо результат.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.clean is True
    assert "99" not in res.text
    assert "600–800 $" in res.text, "обеспеченная цена не имела права погибнуть"
    assert "потім презентуємо результат" in res.text, "хвост клаузы уцелел"


def test_neighbouring_backed_numbers_survive():
    """Требование владельца: редакция не трогает соседние обеспеченные числа."""
    reply = ("SMM-ведення - 750–900 $, стратегія - 600–800 $, "
             "термін 99 днів, працюємо 5–10 робочих днів по логотипу.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    for kept in ("750–900 $", "600–800 $", "5–10 робочих днів"):
        assert kept in res.text, f"{kept} погибло вместе с 99"
    assert "99" not in res.text


def test_redacted_text_is_grammatically_clean():
    reply = ("Стратегія (600–800 $, термін 99 днів, потім презентуємо результат) "
             "— це наш основний напрямок.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")
    t = res.text

    assert ",," not in t and ", ," not in t, f"двойная запятая: {t!r}"
    assert " ," not in t, f"пробел перед запятой: {t!r}"
    assert "()" not in t and "( " not in t.replace("( ", "(", 0), f"пустая скобка: {t!r}"
    assert not re.search(r"\(\s*[,;]", t), f"скобка открывается запятой: {t!r}"
    assert t.count("(") == t.count(")"), f"скобки разъехались: {t!r}"


# C-1 / D2-4 сменил контракт: язык формулы задаёт ЯЗЫК ОТВЕТА, а `language=`
# (скалярка клиента) остаётся только фолбэком «нет сигнала — не менять
# поведение». Прежний сторож брал ОДНУ украинскую реплику и трижды крутил
# скалярку: украинский случай проходил СЛУЧАЙНО — языки совпали, — а два других
# держали контракт, которого больше нет.
#
# Здесь языки разведены НАРОЗЬ: в каждом случае скалярка указывает не на тот
# язык, на котором написан ответ.
_FORMULA_MARK = {
    "uk": "узгоджуємо",
    "ru": "согласовываем",
    "en": "agreed",
}

# Реплики на трёх языках, каждая с ОДНИМ необеспеченным числом (99) и одной
# обеспеченной ценой. Различающие буквы обязательны: детектор читает алфавит,
# и «срок 99 дней» без «ы/э/ъ/ё» неразличим с украинским по построению.
# NB: английская реплика попадает под ЦЕНОВОЕ правило, а не под срочное —
# `_DEADLINE_NUM` знает только «за/через/к», — поэтому маркер `agreed` взят
# такой, что стоит в обеих английских формулах.
_REPLY = {
    "uk": "Стратегія - 600–800 $, термін 99 днів.",
    "ru": "Мы посчитали: стратегия - 600–800 $, срок 99 дней.",
    "en": "The strategy is 600–800 $, delivery in 99 days.",
}


@pytest.mark.parametrize("reply_language,settings_language", [
    ("uk", "ru"),
    ("ru", "uk"),
    ("en", "uk"),
])
def test_replacement_speaks_the_language_of_the_reply(reply_language,
                                                      settings_language):
    """Язык ответа и скалярка РАЗНЫЕ — формула обязана взять язык ответа."""
    res = redact_unbacked(_REPLY[reply_language], KNOWLEDGE,
                          language=settings_language)

    assert _FORMULA_MARK[reply_language] in res.text, (
        f"формула не на языке ответа ({reply_language}): {res.text!r}")
    assert _FORMULA_MARK[settings_language] not in res.text, (
        f"формула заговорила языком скалярки ({settings_language}): "
        f"{res.text!r}")


def test_settings_language_never_decides_the_formula():
    """Сторож ровно на откат контракта: `reply_language = language`.

    Одной удачной пары мало — на ней можно случайно совпасть, как совпал старый
    `[uk-узгоджуємо]`. Проверяем ВСЕ девять пар (три языка ответа × три
    скалярки): язык ответа обязан выигрывать всегда, а формулы двух других
    языков — не появляться ни разу, включая случай, когда скалярка совпала."""
    for reply_language, reply in _REPLY.items():
        for settings_language in _FORMULA_MARK:
            res = redact_unbacked(reply, KNOWLEDGE, language=settings_language)
            where = (f"ответ {reply_language}, скалярка {settings_language} "
                     f"-> {res.text!r}")

            assert _FORMULA_MARK[reply_language] in res.text, (
                "формулу выбрал не язык ответа: " + where)
            for other, mark in _FORMULA_MARK.items():
                if other == reply_language:
                    continue
                assert mark not in res.text, (
                    f"в тексте формула чужого языка ({other}): " + where)


def test_replacement_does_not_promise_owner_contact():
    """Ключевое: редакция НЕ имеет права упоминать владельца/керівницю.

    H2-гейт (run.py) при недоставленной карточке затирает любой ответ, который
    `mentions_owner_contact` считает обещанием контакта владельца, — и наша
    аккуратная редакция превратилась бы обратно в заглушку."""
    from chatter.core.escalation import mentions_owner_contact

    reply = "Стратегія - 600–800 $, термін 99 днів."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")
    assert mentions_owner_contact(
        res.text, owner_id="Ольга", owner_ref="керівницею") is False


def test_clean_flag_never_lies():
    """Инвариант безопасности: clean=True ⇔ в тексте НЕТ необеспеченных чисел.
    Редакция не имеет права выпустить выдуманную цену/срок лиду."""
    samples = [
        "Стратегія - 600–800 $, термін 99 днів.",
        "Зробимо за 99 днів.",
        "Ціна 12345 $ за проєкт.",
        "SMM 750–900 $ за 99 днів.",
        "Все добре, розкажіть про задачу?",
        "Стратегія 600–800 $.",
    ]
    for reply in samples:
        res = redact_unbacked(reply, KNOWLEDGE, language="uk")
        assert res.clean == (not contains_unbacked_claim(res.text, KNOWLEDGE)), \
            f"clean солгал на {reply!r} → {res.text!r}"


def test_backed_reply_is_returned_untouched():
    reply = "Стратегія - 600–800 $, термін 21 календарний день."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.text == reply
    assert res.records == ()
    assert res.clean is True


def test_records_are_pii_free():
    """Каждая редакция логируется — но без текста лида и без текста ответа."""
    reply = "Стратегія - 600–800 $, термін 99 днів, дзвоніть Олені."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.records, "редакция обязана оставить след"
    blob = " ".join(f"{r.number}|{r.rule}|{r.clause_chars}" for r in res.records)
    for leaked in ("Олен", "дзвоніть", "стратегі", "Стратегі"):
        assert leaked not in blob, f"в записи протёк текст: {blob!r}"
    assert "99" in blob and any(r.rule for r in res.records)


def test_collateral_inside_one_clause_is_reported():
    """Честная граница метода: если выдуманное и обеспеченное числа стоят в ОДНОЙ
    клаузе, клауза уходит целиком — обеспеченное гибнет вместе с ним. Это
    по-прежнему НАМНОГО уже, чем подавление всего ответа, но мы это фиксируем."""
    reply = "Пропоную SMM 750–900 $ за 99 днів. Портфоліо надішлю окремо."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.clean is True
    assert "99" not in res.text
    assert "Портфоліо надішлю окремо" in res.text, "соседнее предложение уцелело"


# --- D2-2: редакция в ДИАПАЗОН, а не в отсылку к владельцу -------------------
#
# Спека sales-competence §4 D2-2. Мотив прямой: «точну вартість узгоджуємо
# індивідуально» — это ОТСЫЛКА, она читается лидом как «мне не ответили».
# Если для той же услуги в knowledge есть законная вилка, лид обязан получить
# ЕЁ, а не адрес владельца. При неоднозначности — падаем на старую формулу.


def test_redaction_gives_the_range_instead_of_a_referral():
    """Услуга в клаузе опознана → вместо отсылки уходит вилка из knowledge."""
    reply = "SMM-ведення обійдеться вам у 1500 $ на місяць."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.clean is True
    assert "1500" not in res.text, "выдуманная цена обязана уйти"
    assert "750–900 $" in res.text, "вилка из knowledge не подставлена"
    assert "узгоджуємо індивідуально" not in res.text,         "отсылка к владельцу осталась там, где есть законная вилка"


def test_range_substitution_speaks_the_language_of_the_reply():
    """D2-4 не отменяется: приставка к вилке — на языке ОТВЕТА."""
    res = redact_unbacked("SMM-ведение обойдётся вам в 1500 $ в месяц.",
                          KNOWLEDGE, language="uk")
    assert "750–900 $" in res.text
    assert "ориентировочно" in res.text.casefold(),         "русский ответ получил не русскую приставку"


# --- встречные сторожа: без них подстановка вилки станет враньём -------------

def test_unknown_service_still_falls_back_to_the_old_formula():
    """Услуги в knowledge нет → вилку брать НЕОТКУДА, отсылка остаётся.

    Без этого сторожа реализация «подставить первую попавшуюся вилку» прошла бы
    позитивный тест и назвала бы цену SMM за фотосессию."""
    reply = "Фотосесія обійдеться вам у 1500 $."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert "1500" not in res.text
    assert "узгоджуємо індивідуально" in res.text,         "для неизвестной услуги подставлена чужая вилка"
    assert "750–900" not in res.text and "600–800" not in res.text


def test_ambiguous_service_falls_back_to_the_old_formula():
    """Клауза называет ДВЕ услуги — какая из вилок её, неизвестно."""
    reply = "SMM-ведення і маркетингова стратегія разом коштуватимуть 1500 $."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert "1500" not in res.text
    assert "узгоджуємо індивідуально" in res.text,         "при двух услугах выбрана одна вилка — это угадывание"


def test_deadline_clause_never_gets_a_price_range():
    """Срочная клауза остаётся срочной формулой: вилка цен там — бессмыслица."""
    reply = "Маркетингова стратегія — 600–800 $, зробимо за 99 днів."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert "99" not in res.text
    assert "термін узгоджуємо індивідуально" in res.text
    assert "600–800 $" in res.text, "обеспеченная цена не имела права погибнуть"


def test_substituted_range_is_itself_backed():
    """Подставленная вилка обязана проходить тот же гардрейл (clean не врёт)."""
    res = redact_unbacked("SMM-ведення обійдеться вам у 1500 $ на місяць.",
                          KNOWLEDGE, language="uk")
    assert res.clean is True
    assert not contains_unbacked_claim(res.text, KNOWLEDGE),         "подставленная вилка сама читается как выдумка"


def test_price_line_tail_never_leaks_into_the_reply():
    """Из строки прайса берём ДЕНЕЖНУЮ ГОЛОВУ, а не весь хвост.

    В боевом knowledge у SMM за вилкой идёт «. Ціна за місяць, мінімальний
    строк співпраці — від 2 місяців». Утащить это в реплику значит выдать
    справочник за ответ — и заодно внести в текст число (2), которого клаузе
    никто не обеспечивал."""
    knowledge = ("# Ціни" + chr(10) +
                 "- SMM-ведення — 750–900 $. Ціна за місяць, мінімальний "
                 "строк співпраці — від 2 місяців" + chr(10))
    res = redact_unbacked("SMM-ведення обійдеться вам у 1500 $ на місяць.",
                          knowledge, language="uk")

    assert "750–900 $" in res.text
    assert "мінімальний строк" not in res.text, "хвост строки прайса утёк в реплику"
    assert res.clean is True
