# -*- coding: utf-8 -*-
"""Гардиан клиентской панели: С5, С6, С8, С9, С10 и ps1-половина С7.

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md, §2, §2.1,
§2.2, §2.3 (вариант B: отдельный гардиан-скрипт + задача, как у трёх соседей).

Редакция спеки от 20.08 отменила одно требование и добавила три. Отменено:
«после MAX_REFUSALS прекратить попытки». Владелец назвал правило целиком —
после трёх отказов гардиан уходит на LONG_RETRY_S = 1800 с и продолжает
пытаться ВСЕГДА, алерт при этом ровно один, а в лог пишется каждая попытка со
своей причиной. Добавлено §2.3: пропавший тайнет — это «не могу измерить», а
не «мертва».

Сторожа написаны ОТ СПЕКИ, планового кода автор не видел.

Контракт, который они фиксируют:

    scripts/panel_client_guardian_detached.ps1
        param(... [string]$Root, [string]$Slug, [int]$Port,
              [int]$BackoffSeconds = 300, [int]$LongRetrySeconds = 1800,
              [int]$MaxRefusals = 3, [switch]$NoLoop)

        Test-ShouldStartPanel -Reason <string> -Refusals <int>
                              -SinceRefusalSec <int>            -> $true/$false
        Step-RefusalState -Outcome <ok|refused|unmeasurable>
                          -Refusals <int> -Alerted <bool>
                              -> @{Refusals; Alerted; Alert}
        Format-RefusalLine -Refusal <текст> -Refusals <int>      -> строка
        Test-ShouldLogUnmeasurable -WasUnmeasurable <bool>
                                   -IsUnmeasurable <bool>       -> $true/$false

`-Reason` — вокабуляр ПРОБЫ (§3): `ok`, `no_response`, `http:<код>`,
`no_bind_address`. Булев «жив / не жив» отброшен осознанно: §2.3 ввёл ТРЕТЬЕ
состояние, а слипание «не измерил» с «мертва» — это и есть тот дефект.

`-Refusal` у `Format-RefusalLine` — это НЕ `-Reason`, и имя разведено
намеренно. Вокабуляр пробы — машинный ярлык состояния; отказ старта —
человеческий текст из stdout панели («JARVIS_PANELS_KEY не задан…»). Одно
имя на два несовместимых понятия в одном файле означает, что первый же, кто
передаст в журнал `no_response`, получит бессмысленную строку и не поймёт,
почему.

`-NoLoop` — тестовый крюк, как у соседей (`bot_guardian_detached.ps1`,
`chatter_guardian_detached.ps1`): дот-сорс определяет функции и НЕ берёт лок,
НЕ крутит цикл и НЕ трогает C:\\jarvis.

Почему решение обязано быть ЧИСТОЙ функцией: «поднимать / не поднимать» — это
и есть весь гардиан. Проверять надо решение, а не то, что процесс как-то
поднялся; иначе бесконечный цикл перезапуска на кривом окружении выглядит в
логе точно так же, как здоровая работа, только чаще.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="гардиан Windows-only (PowerShell + Get-NetTCPConnection)")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "panel_client_guardian_detached.ps1"
REGISTRAR = REPO_ROOT / "scripts" / "register_panel_client_guardian.ps1"
PORT_OWNER = REPO_ROOT / "scripts" / "stop_port_owner.ps1"
TASK_NAME = "JarvisPanelClientGuardian"


def _run_ps(body: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """Дот-сорс НАСТОЯЩЕГО .ps1 с -NoLoop и чужим Root — тот же приём, что в
    tests/test_bot_guardian_exit_code.py и test_chatter_guardian_multiclient.py."""
    assert SCRIPT.exists(), (
        "нет скрипта %s — присмотра за клиентской панелью не существует"
        % SCRIPT)
    command = ". '%s' -Root '%s' -NoLoop\n%s\n" % (SCRIPT, root, body)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def _decide(root, reason: str, refusals: int, since: int) -> bool:
    """Вердикт чистой функции гардиана, приведённый к bool.

    `-Reason` — вокабуляр ПРОБЫ (§3): `ok` (панель отвечает), `no_response`,
    `http:<код>`, `no_bind_address`. Булев `-Alive` здесь больше не годится:
    §2.3 добавил ТРЕТЬЕ состояние — «не могу измерить», — а два значения трёх
    состояний не выражают. Именно на слипании «не измерил» с «мертва» и
    построен дефект, который §2.3 закрывает."""
    body = ("Test-ShouldStartPanel -Reason '%s' -Refusals %d "
            "-SinceRefusalSec %d" % (reason, refusals, since))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines, ("Test-ShouldStartPanel ничего не вернула: %r / %r"
                   % (run.stdout, run.stderr))
    verdict = lines[-1]
    assert verdict in ("True", "False"), (
        "решение обязано быть булевым, получено %r (весь вывод: %r)"
        % (verdict, run.stdout))
    return verdict == "True"


def _step(root, outcome: str, refusals: int, alerted: bool):
    """Шаг состояния отказов: (Refusals, Alerted, Alert).

    `-Outcome`: `ok` — панель поднялась; `refused` — `run_panel_client`
    вернул rc 1; `unmeasurable` — адрес бинда не определить (§2.3).
    Возвращается ТРОЙКА, потому что «сколько отказов», «объявляли ли» и
    «объявить сейчас» — три разных вопроса, и склеивание любых двух и есть
    механизм, который С8 проверяет."""
    body = ("$s = Step-RefusalState -Outcome '%s' -Refusals %d -Alerted $%s\n"
            "'{0}|{1}|{2}' -f $s.Refusals, $s.Alerted, $s.Alert"
            % (outcome, refusals, str(alerted).lower()))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines, ("Step-RefusalState ничего не вернула: %r / %r"
                   % (run.stdout, run.stderr))
    parts = lines[-1].split("|")
    assert len(parts) == 3, (
        "Step-RefusalState обязана вернуть Refusals/Alerted/Alert, получено %r"
        % (lines[-1],))
    return int(parts[0]), parts[1] == "True", parts[2] == "True"


def _log_line(root, refusal: str, refusals: int) -> str:
    """Строка журнала одной неудавшейся попытки.

    `-Refusal` — человеческий текст причины из stdout панели, а не ярлык
    состояния из вокабуляра пробы (см. шапку модуля)."""
    body = ("Format-RefusalLine -Refusal '%s' -Refusals %d"
            % (refusal.replace("'", "''"), refusals))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    out = run.stdout.strip()
    assert out, ("Format-RefusalLine ничего не вернула: %r / %r"
                 % (run.stdout, run.stderr))
    return out


# ---------------- С5: решение «поднимать / не поднимать» ----------------

def test_a_live_panel_is_left_alone(tmp_path):
    """Живую не трогаем. Гардиан, который «на всякий случай» перезапускает
    здоровое, — это ежеминутный обрыв сессии клиента."""
    assert _decide(tmp_path, reason="ok", refusals=0, since=0) is False


def test_a_dead_panel_is_raised_at_once(tmp_path):
    """Первый и главный случай: 20.08 панель сняли снаружи вместе с
    родительской сессией. Никакого backoff здесь нет — отказов не было."""
    assert _decide(tmp_path, reason="no_response", refusals=0, since=0) is True


def test_a_wedged_panel_is_restarted_too(tmp_path):
    """§2 п.1: только процесс — недостаточно, зависший uvicorn держит порт и
    молчит. Живой процесс с плохим ответом обязан пойти на перезапуск, иначе
    порт занят навсегда, а клиент видит тишину."""
    assert _decide(tmp_path, reason="http:500", refusals=0, since=0) is True


def test_a_refusal_is_not_retried_immediately(tmp_path):
    """§2.1: rc 1 — это осознанный fail-closed, а не падение. Перезапускать
    его раз в 15 секунд значит засыпать лог и прятать настоящую причину."""
    assert _decide(tmp_path, reason="no_response", refusals=1, since=10) is False


def test_the_backoff_boundary_is_inclusive(tmp_path):
    """`BACKOFF_S` = 300 с, и граница ВКЛЮЧИТЕЛЬНА (уточнение владельца).

    Названа отдельным сторожем намеренно: `>` вместо `>=` — не опечатка, а
    лишний цикл ожидания, который никто никогда не заметит в логе."""
    assert _decide(tmp_path, reason="no_response", refusals=1, since=299) is False
    assert _decide(tmp_path, reason="no_response", refusals=1, since=300) is True


def test_a_refusal_is_retried_after_the_backoff(tmp_path):
    """Пауза обязана КОНЧАТЬСЯ: владелец вернул ключ — панель поднимается
    сама, без похода в планировщик."""
    assert _decide(tmp_path, reason="no_response", refusals=1, since=600) is True


def test_the_second_refusal_still_gets_a_third_chance(tmp_path):
    """ГРАНИЦА снизу: уйти на длинный интервал раньше третьего отказа значит
    держать панель мёртвой полчаса там, где она поднялась бы через пять минут."""
    assert _decide(tmp_path, reason="no_response", refusals=2, since=600) is True


def test_three_refusals_move_to_the_long_interval(tmp_path):
    """Сердцевина обновлённого С5 (§2.2). После третьего отказа пауза растёт
    с 300 с до `LONG_RETRY_S` = 1800 с: 600 секунд уже хватало раньше и не
    хватает теперь."""
    assert _decide(tmp_path, reason="no_response", refusals=3, since=600) is False


def test_the_long_interval_boundary_is_inclusive(tmp_path):
    """1800 с ровно — попытка разрешена."""
    assert _decide(tmp_path, reason="no_response", refusals=3, since=1799) is False
    assert _decide(tmp_path, reason="no_response", refusals=3, since=1800) is True


def test_the_attempts_never_stop(tmp_path):
    """🔴 ОТМЕНЁННОЕ ПОВЕДЕНИЕ. Первая редакция §2.1 требовала «прекратить
    попытки», и сторож на вечный стоп существовал. Владелец правило заменил:
    «жёсткий стоп требует человека, а после переезда „подойти к машине“
    перестанет быть вариантом. Мы это уже проходили 16.08 — гардиан вошёл в
    состояние DOWN и не вышел 13 часов 42 минуты».

    Теперь обязателен ОБРАТНЫЙ сторож: сколько бы отказов ни накопилось,
    очередная попытка через `LONG_RETRY_S` состоится. Иначе §5 п.4 приёмки
    («вернуть ключ → панель поднимается САМА») невыполнима в принципе."""
    assert _decide(tmp_path, reason="no_response", refusals=3, since=1800) is True
    assert _decide(tmp_path, reason="no_response", refusals=9, since=1800) is True
    assert _decide(tmp_path, reason="no_response", refusals=99, since=86400) is True


def test_a_live_panel_wins_over_any_refusal_history(tmp_path):
    """Живая панель не поднимается повторно ни при каком счётчике отказов —
    иначе три отказа в прошлом приводили бы к перезапуску здоровой."""
    assert _decide(tmp_path, reason="ok", refusals=3, since=0) is False
    assert _decide(tmp_path, reason="ok", refusals=1, since=600) is False
    assert _decide(tmp_path, reason="ok", refusals=99, since=86400) is False


# --------- С8: ОДНА запись журнала на вход в состояние, не поток ---------
#
# КАКОЙ ИМЕННО «алерт». Спека §2.2 называет канал вслух, и это НЕ телеграм:
# гардиан пишет ОДНУ строку в свой журнал на вход в состояние, а сообщение
# владельцу шлёт проба §3 через уже существующие `evaluate`/`alerted` в
# `ops_watchdog` — там дебаунс и однократность сделаны давно и проверены.
# Второй читатель токена в PowerShell был бы вторым числом на ту же вещь
# ровно там, где мы их вычищаем.
#
# Поэтому `Alert` ниже — это «пора записать строку о входе», а не «отправь в
# TG». Сторожа проверяют ОДНОКРАТНОСТЬ, а не транспорт: гардиан не обязан и
# не имеет права уметь в телеграм.

def test_no_alert_before_the_state_is_entered(tmp_path):
    """Два отказа — это ещё рабочая ситуация (владелец правит `.env`).
    Отметка о входе на первом же отказе вернула бы нас к шуму, от которого
    уходим."""
    refusals, alerted, alert = _step(tmp_path, "refused", refusals=0, alerted=False)
    assert (refusals, alerted, alert) == (1, False, False)
    refusals, alerted, alert = _step(tmp_path, "refused", refusals=1, alerted=False)
    assert (refusals, alerted, alert) == (2, False, False)


def test_the_third_refusal_alerts_once(tmp_path):
    """ВХОД в состояние исчерпанных отказов — единственный момент, который
    гардиан отмечает в журнале отдельной строкой."""
    refusals, alerted, alert = _step(tmp_path, "refused", refusals=2, alerted=False)
    assert refusals == 3, refusals
    assert alert is True, "вход в состояние прошёл молча"
    assert alerted is True, "состояние не запомнило, что уже отмечало"


def test_the_tenth_and_the_hundredth_attempt_stay_silent(tmp_path):
    """Сердцевина С8. Довод владельца дословно: «повторяющееся сообщение
    перестают читать, мы это видели на 25 тестовых вопросах».

    Постоянно красной пробы §3 достаточно — она и есть непрерывный сигнал,
    и она же (а не гардиан) шлёт владельцу телеграм."""
    for n in (3, 4, 9, 10, 99, 100):
        _r, _a, alert = _step(tmp_path, "refused", refusals=n, alerted=True)
        assert alert is False, (
            "попытка №%d дала повторную запись о входе — за сутки их будет 48"
            % (n + 1))


def test_a_successful_start_clears_the_state(tmp_path):
    """Парный сторож, без которого С8 проходит по ОШИБОЧНОЙ причине.

    «Алертили однажды за всю жизнь процесса» — это ДРУГОЙ механизм, и он
    выглядит зелёным на всех проверках выше. Различает их только успешный
    подъём: он обязан обнулить и счётчик, и признак «уже отмечали»."""
    refusals, alerted, alert = _step(tmp_path, "ok", refusals=5, alerted=True)
    assert refusals == 0, "счётчик отказов не обнулён успешным подъёмом"
    assert alerted is False, (
        "признак «уже отмечали» пережил успешный подъём — следующая авария "
        "пройдёт молча")
    assert alert is False, "успешный подъём не повод для записи о входе"


def test_the_next_entry_alerts_again(tmp_path):
    """Продолжение парного: после обнуления третий отказ снова обязан дать
    строку о входе. Иначе вторая авария за сутки будет невидимой."""
    refusals, alerted, _alert = _step(tmp_path, "ok", refusals=5, alerted=True)
    for _ in range(2):
        refusals, alerted, alert = _step(tmp_path, "refused",
                                         refusals=refusals, alerted=alerted)
        assert alert is False, refusals
    refusals, alerted, alert = _step(tmp_path, "refused",
                                     refusals=refusals, alerted=alerted)
    assert (refusals, alert) == (3, True), (refusals, alerted, alert)


# ---------------- С9: лог на КАЖДОЙ попытке, и он несёт ПРИЧИНУ ----------

# Тексты отказа — дословно те, что печатает `run_panel_client.py` перед
# rc 1. Это ДРУГОЙ словарь, чем вердикты пробы, и в том вся суть
# переименования параметра.
REFUSAL_KEY = "JARVIS_PANELS_KEY_YARINA не задан"
REFUSAL_OWNER = "ключ инстанса СОВПАДАЕТ с ключом владельца"


def test_the_log_line_carries_the_reason_verbatim(tmp_path):
    """§2.2: «в лог при КАЖДОЙ попытке — короткая строка с причиной отказа».
    Причина обязана быть В СТРОКЕ, а не в соседнем файле и не в голове."""
    line = _log_line(tmp_path, REFUSAL_KEY, refusals=1)
    assert REFUSAL_KEY in line, line


def test_two_different_reasons_give_two_different_lines(tmp_path):
    """🔴 Сердцевина С9. Сторож на КОНСТАНТУ («попытка не удалась») обязан
    краснеть: весь смысл требования — через час отличить «одна и та же
    ошибка» от «разные». Одинаковые строки этого не дают, сколько бы их ни
    было."""
    a = _log_line(tmp_path, REFUSAL_KEY, refusals=1)
    b = _log_line(tmp_path, REFUSAL_OWNER, refusals=2)
    assert a != b, (
        "две разных причины дали ОДНУ строку %r — журнал не отличает "
        "«всё та же ошибка» от «уже другая»" % (a,))
    assert REFUSAL_OWNER in b, b


def test_the_reason_is_not_truncated_away(tmp_path):
    """«Короткая строка» не значит «обрезанная причина»: два отказа, чьи
    тексты расходятся только в конце, обязаны остаться различимыми."""
    long_a = "TAMAPI_DB не задан: панель показала бы ЧУЖУЮ переписку (volska)"
    long_b = "TAMAPI_DB не задан: панель показала бы ЧУЖУЮ переписку (demo)"
    assert _log_line(tmp_path, long_a, 1) != _log_line(tmp_path, long_b, 1)


def test_the_guardian_writes_to_its_own_journal(tmp_path):
    """§5 п.5: подопечный и присматривающий пишут в РАЗНЫЕ файлы. Один
    журнал на двоих означает, что разбор смерти панели идёт по логу того, кто
    её поднимал."""
    code = _guardian_code()
    assert "panel_client_guardian.stdout.log" in code, (
        "гардиан не пишет собственный журнал — причины отказов негде читать")


# ---------------- С10: «не могу измерить» ≠ «мертва» (§2.3) ----------------

def test_an_unmeasurable_address_never_starts_anything(tmp_path):
    """Сердцевина С10. Тайнет пропал → `resolve_client_host` МОЛЧА падает на
    петлю → проба не достучалась. Панель при этом ЖИВА на старом адресе.

    Перезапуск здесь — убийство здоровой панели, ровно тот класс, что стоил
    простоя 16.08."""
    assert _decide(tmp_path, reason="no_bind_address", refusals=0, since=0) is False


def test_an_unmeasurable_address_stays_hands_off_forever(tmp_path):
    """Ни счётчик отказов, ни сколь угодно долгое ожидание не превращают
    «не могу измерить» в «поднимай». Иначе через полчаса живую панель всё
    равно снесут — просто позже."""
    assert _decide(tmp_path, reason="no_bind_address",
                   refusals=3, since=1800) is False
    assert _decide(tmp_path, reason="no_bind_address",
                   refusals=99, since=86400) is False


def test_a_dead_panel_is_still_raised_when_the_address_is_known(tmp_path):
    """ГРАНИЦА С10. Запрет обязан касаться ТОЛЬКО `no_bind_address`: если
    он расползётся на `no_response`, гардиан перестанет поднимать вообще
    что-либо, и все сторожа §2.3 останутся зелёными."""
    assert _decide(tmp_path, reason="no_response", refusals=0, since=0) is True


def test_an_unmeasurable_cycle_does_not_spend_the_refusal_counter(tmp_path):
    """«Не могу измерить» — не отказ старта: старт не запускался вовсе.

    Если такие циклы копятся в счётчике, пропавший на два часа тайнет сам
    доведёт гардиана до состояния исчерпанных отказов и пришлёт 🚨 о том,
    чего не было."""
    refusals, alerted, alert = _step(tmp_path, "unmeasurable",
                                     refusals=2, alerted=False)
    assert refusals == 2, "счётчик отказов вырос без единой попытки старта"
    assert alerted is False and alert is False, (alerted, alert)


def _should_log_unmeasurable(root, was: bool, now: bool) -> bool:
    """Пора ли писать строку про «не могу измерить»."""
    body = ("Test-ShouldLogUnmeasurable -WasUnmeasurable $%s -IsUnmeasurable $%s"
            % (str(was).lower(), str(now).lower()))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines, ("Test-ShouldLogUnmeasurable ничего не вернула: %r / %r"
                   % (run.stdout, run.stderr))
    assert lines[-1] in ("True", "False"), lines[-1]
    return lines[-1] == "True"


def test_the_unmeasurable_state_is_logged_on_entry_and_on_exit(tmp_path):
    """§2.3: строка на ВХОД и строка на ВЫХОД. Две штуки — это и есть весь
    след того, что тайнет пропадал: когда началось и когда кончилось."""
    assert _should_log_unmeasurable(tmp_path, was=False, now=True) is True
    assert _should_log_unmeasurable(tmp_path, was=True, now=False) is True


def test_the_unmeasurable_state_is_not_logged_every_cycle(tmp_path):
    """🔴 Сердцевина: требование §2.2 «строка на каждой попытке» сюда НЕ
    распространяется — попыток здесь нет по определению, старт не запускался.

    Цикл 15 с: за ночь ежецикловая запись дала бы 2880 строк и похоронила бы
    в журнале всё остальное, включая настоящие причины отказов."""
    assert _should_log_unmeasurable(tmp_path, was=True, now=True) is False


def test_a_measurable_cycle_writes_nothing_about_the_address(tmp_path):
    """ГРАНИЦА: обычный здоровый цикл не имеет права оставлять след «адрес
    в порядке» — это те же 2880 строк, только с другим текстом."""
    assert _should_log_unmeasurable(tmp_path, was=False, now=False) is False


def test_the_launch_never_hardcodes_the_port_it_starts_on():
    """Подъём идёт с `--port <переменная>`, а не с числом.

    Требование — про ЧЕТВЁРТОЕ МЕСТО для порта, а не про орфографию: число,
    вписанное в команду запуска, переживёт правку параметра, и панель встанет
    на одном порту, а жива она или нет будут решать по другому — гардиан
    вечно поднимает живую поверх живой.

    Имя переменной сторожа НЕ касается. `$Port`, `$PanelPort` — одинаково
    законно, значение всё равно приходит из одного места. Сторож, который
    запрещает переименование, приучает себя отключать: ровно этой болезнью
    болел прежний структурный С7, и её уже пришлось лечить."""
    code = _guardian_code()
    spots = list(re.finditer(r"--port", code))
    assert spots, (
        "гардиан поднимает панель без --port: она встанет на умолчание "
        "запускающего, а вердикт вынесен по своему числу")
    for m in spots:
        tail = code[m.end():m.end() + 60]
        assert re.search(r"\$\w+", tail), (
            "после --port стоит не переменная: %r" % (tail.strip()[:40],))
        literal = re.search(r"(?<![\w$])\d{3,5}(?![\w])", tail)
        assert not literal, (
            "после --port зашито число %s — четвёртое место для порта, "
            "невидимое С11" % literal.group(0))


def test_every_launch_uses_one_and_the_same_port_variable():
    """Усиление, которое ловит настоящий дефект, а не имя.

    Если подъём встречается в скрипте не один раз (например, отдельная ветка
    первого старта), все вхождения обязаны брать порт из ОДНОЙ переменной.
    Две разных — это «подняли на одном, судим по другому» внутри одного
    файла, и никакой сторож на литералы этого не заметит."""
    code = _guardian_code()
    used = set()
    for m in re.finditer(r"--port", code):
        var = re.search(r"\$(\w+)", code[m.end():m.end() + 60])
        if var:
            used.add(var.group(1))
    assert len(used) <= 1, (
        "порт подъёма берётся из разных переменных: %s" % sorted(used))


def test_the_guardian_knows_the_verdict_by_name():
    """Слово должно существовать в скрипте: без ветки `no_bind_address`
    вердикт слипается с `no_response` — и §2.3 просто не реализован."""
    code = _guardian_code()
    assert "no_bind_address" in code, (
        "гардиан не различает «не могу измерить» и «мертва» (§2.3)")


# ---------------- С6: освобождение порта — по ВЛАДЕЛЬЦУ порта ----------------

_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.S)


def _ps_code(text: str) -> str:
    """Текст скрипта БЕЗ комментариев.

    Снимать комментарии обязательно: спека §2.1 запрещает матч по подстроке
    ИМЕННО ТАКИМИ словами, и добросовестный автор перепишет этот запрет в
    шапку скрипта. Сторож, читающий комментарии, покраснел бы на объяснении,
    почему так делать нельзя, — тот же класс, что «сообщение коммита это
    ДАННЫЕ, а не команда»."""
    text = _BLOCK_COMMENT.sub(" ", text)
    out = []
    for line in text.splitlines():
        buf, quote = [], None
        for ch in line:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            if ch == "#":
                break
            buf.append(ch)
        out.append("".join(buf))
    return "\n".join(out)


# Каждый образец — способ снести ЧУЖОЙ процесс. DEV-38 (18.08): pytest из
# worktree позвал тамошний скрипт, тот убил боевого раннера по глобальной
# подстроке. Под матч `run_panel_client` попадут: панель другого клиента,
# сам pytest, который её импортирует, и любая ручная сессия.
FORBIDDEN = [
    (r"CommandLine\s*-(match|like|eq|ne|contains|in)\b",
     "матч по командной строке процесса"),
    (r"-(match|like)\s*[\"'][^\"'\r\n]*run_panel_client",
     "матч по подстроке 'run_panel_client'"),
    (r"-(match|like)\s*[\"'][^\"'\r\n]*panel_client_guardian",
     "матч по собственному имени — гардиан найдёт САМ СЕБЯ"),
    (r"Where-Object[^\r\n]*CommandLine", "фильтр процессов по командной строке"),
    (r"Get-Process\b[^|\r\n]*python", "выбор процессов по имени интерпретатора"),
    (r"Stop-Process\s+-Name\b", "остановка по ИМЕНИ процесса, а не по PID"),
    (r"taskkill[^\r\n]*/IM\b", "taskkill по имени образа"),
]


def _guardian_code() -> str:
    assert SCRIPT.exists(), "нет скрипта %s" % SCRIPT
    return _ps_code(SCRIPT.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("pattern,what", FORBIDDEN,
                         ids=[w for _p, w in FORBIDDEN])
def test_the_guardian_never_selects_processes_by_a_substring(pattern, what):
    """С6. Сторож СТАТИЧЕСКИЙ намеренно: живым прогоном такую дыру ловят
    только тогда, когда рядом случайно оказался чужой процесс, — то есть
    в проде и один раз."""
    code = _guardian_code()
    hit = re.search(pattern, code, re.I)
    assert not hit, (
        "в гардиане появилось %s: %r\nПорт освобождается ТОЛЬКО по владельцу "
        "(Get-NetTCPConnection -LocalPort <port> -> OwningProcess)."
        % (what, hit.group(0)))


def test_the_guardian_frees_the_port_by_its_owner():
    """Обратная сторона запрета: адресация обязана СУЩЕСТВОВАТЬ.

    Один запрет без требования выполняется удалением освобождения порта
    вовсе — и тогда зависший uvicorn держит 8011, а новый не встаёт."""
    code = _guardian_code()
    own = re.search(r"Get-NetTCPConnection", code, re.I)
    delegated = re.search(r"stop_port_owner", code, re.I)
    assert own or delegated, (
        "гардиан не освобождает порт по его владельцу и не зовёт "
        "scripts/stop_port_owner.ps1")
    if own:
        assert re.search(r"OwningProcess", code, re.I), (
            "Get-NetTCPConnection есть, а PID владельца не берётся")
        assert re.search(r"-LocalPort", code, re.I), (
            "порт не назван — соединения выбираются чем-то другим")


def test_the_delegate_is_itself_addressed_by_pid():
    """Если освобождение делегировано `stop_port_owner.ps1`, запрет обязан
    держаться и ТАМ: иначе С6 обходится одним вызовом."""
    code = _ps_code(PORT_OWNER.read_text(encoding="utf-8-sig"))
    assert re.search(r"Get-NetTCPConnection", code, re.I), code[:300]
    assert re.search(r"OwningProcess", code, re.I), code[:300]
    assert re.search(r"Stop-Process\s+-Id\b", code, re.I), code[:300]
    for pattern, what in FORBIDDEN:
        assert not re.search(pattern, code, re.I), (what, PORT_OWNER)


def test_the_guardian_does_not_look_for_itself(tmp_path):
    """Ловушка «запуск ≠ упоминание»: строка-маркер попадает в командную
    строку ИСКАТЕЛЯ, и панель отрицала бы свой же процесс.

    Спека §2.1 закрывает это ПО ПОСТРОЕНИЮ: решение принимается по порту и
    HTTP-ответу, поэтому cmdline в скрипте не упоминается вовсе."""
    code = _guardian_code()
    assert not re.search(r"\bCommandLine\b", code, re.I), (
        "гардиан читает командные строки процессов — значит найдёт и себя")


# ---------------- ps1-половина С7: один источник адреса ----------------

def test_the_guardian_takes_the_address_from_the_shared_resolver():
    """§3.1: адрес пробы и адрес бинда — ОДНА функция.

    Гардиан обязан проверять живость по ТОМУ ЖЕ адресу, на который панель
    биндится. Взять петлю здесь — значит перезапускать здоровую панель
    каждые 15 секунд: на 127.0.0.1 её нет никогда."""
    code = _guardian_code()
    assert re.search(r"resolve_client_host", code), (
        "гардиан не спрашивает адрес у run_panel_client.resolve_client_host")


def test_the_guardian_hardcodes_no_address():
    """Два числа на одну вещь разойдутся ровно тогда, когда адрес тайнета
    сменится, — и гардиан начнёт поднимать живую панель поверх живой."""
    code = _guardian_code()
    literals = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", code)
    assert not literals, (
        "в гардиане зашит IPv4-литерал %s вместо общего резолвера" % (literals,))


# ---------------- задача: присмотр, который РЕАЛЬНО зарегистрирован ----------

def test_the_registrar_exists_and_names_the_task():
    """Спека §1: «незапущенная задача выглядит как присмотр, но им не
    является». Регистратор — часть арки, а не следующий шаг."""
    assert REGISTRAR.exists(), "нет регистратора %s" % REGISTRAR
    text = REGISTRAR.read_text(encoding="utf-8-sig")
    assert TASK_NAME in text, text[:400]
    assert "panel_client_guardian_detached.ps1" in text, text[:400]


def test_the_task_survives_logoff_and_reboot_like_its_three_neighbours():
    """S4U / Highest / IgnoreNew, триггеры AtStartup + AtLogOn (§2).

    Панель умерла ровно от того, что жила в интерактивной сессии. Задача без
    S4U повторила бы это через один разлогин."""
    text = REGISTRAR.read_text(encoding="utf-8-sig")
    for token in ("S4U", "Highest", "IgnoreNew", "AtStartup", "AtLogOn"):
        assert token in text, (
            "регистратор не задаёт %s — присмотр не переживёт разлогин или "
            "ребут" % token)


def test_the_guardian_holds_a_pid_lock_per_slug():
    """§2 п.3: PID-лок `state/locks/panel_client_guardian_<slug>.pid`.

    Ловушка 3 из chatter-гардиана: PID-файл переживает kill, поэтому сверять
    надо И номер, И что процесс — powershell. Иначе номер переиспользуется
    системой, и второй гардиан не стартует НИКОГДА."""
    code = _guardian_code()
    assert re.search(r"panel_client_guardian.*\.pid", code, re.I), (
        "нет PID-лока — два гардиана поднимут две панели на один порт")
    assert re.search(r"powershell|pwsh", code, re.I), (
        "лок сверяет только номер PID: после kill номер переиспользуется, и "
        "живой гардиан больше не стартует")
