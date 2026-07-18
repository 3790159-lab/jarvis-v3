from chatter.core.follow_up import promises_return


def test_detects_core_return_promises_ru():
    assert promises_return("Хороший вопрос — уточню детали и вернусь.")
    assert promises_return("Отвечу позже, как узнаю.")
    assert promises_return("Дам знать, как только выясню.")
    assert promises_return("Напишу вам, когда будет ответ.")


def test_detects_return_promises_uk_en():
    assert promises_return("Добре, повернуся з відповіддю.")
    assert promises_return("Відповім пізніше.")
    assert promises_return("Sure, I'll get back to you.")
    assert promises_return("I'll let you know.")


def test_negate_ball_in_lead_court_is_not_a_promise():
    assert not promises_return("Вернёмся к этому, когда определитесь.")
    assert not promises_return("Как решите — дайте знать когда удобно.")


def test_plain_reply_is_not_a_return_promise():
    assert not promises_return("Съёмка стоит 15000 грн.")
    assert not promises_return("Наверное, вам подойдёт портретная съёмка.")
    assert not promises_return("")
    assert not promises_return("Верните деньги за съёмку.")   # «верните» — слово лида, не обещание Ани
