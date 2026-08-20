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

# Обратная кавычка + перевод строки — продолжение ОДНОГО вызова
# PowerShell. Собран отдельной константой: в литерале внутри функции
# экран уже однажды был съеден ПРИ ЗАПИСИ ФАЙЛА (память про TAILSCALE_EXE).
# Обратная кавычка + перевод строки — продолжение ОДНОГО вызова PowerShell.
# Собрано ИЗ ЧАСТЕЙ, без экранов в литерале: экран уже однажды был съеден
# ПРИ ЗАПИСИ ФАЙЛА и превратился в другой символ молча (TAILSCALE_EXE).
_SPACE = chr(92) + "s"
_JOIN_CONTINUATION = re.compile(
    chr(96) + "[ " + chr(9) + "]*" + chr(92) + "r?" + chr(92) + "n[ " + chr(9) + "]*")

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

def _registrar_code() -> str:
    """Регистратор БЕЗ комментариев.

    🔴 ДЫРА, найденная мутационным гейтом. Прежний сторож искал подстроку в
    СЫРОМ тексте. Замена `-LogonType S4U` на `-LogonType Interactive` в
    НАСТОЯЩЕМ вызове `New-ScheduledTaskPrincipal` оставляет слово «S4U» в
    шапке-комментарии и в финальном `Write-Host` — сторож зелёный, а задача
    перестаёт переживать разлогин. То есть панель умирает ровно тем способом,
    ради которого вся арка и заведена.

    Грep не отличает код от комментария и от сообщения человеку. В этом вся
    болезнь, и она уже стоила нам одного круга: «сообщение — ДАННЫЕ, а не
    команда»."""
    assert REGISTRAR.exists(), "нет регистратора %s" % REGISTRAR
    return _ps_code(REGISTRAR.read_text(encoding="utf-8-sig"))


def test_the_registrar_exists_and_names_the_task():
    """Спека §1: «незапущенная задача выглядит как присмотр, но им не
    является». Регистратор — часть арки, а не следующий шаг."""
    code = _registrar_code()
    assert TASK_NAME in code, (
        "имя задачи не встречается в КОДЕ регистратора (только в тексте?)")
    assert "panel_client_guardian_detached.ps1" in code, code[:400]


def _registrar_statements() -> str:
    """Код регистратора, склеенный по ЛОГИЧЕСКИМ строкам.

    PowerShell продолжает вызов обратной кавычкой, и `New-ScheduledTaskPrincipal`
    с `-LogonType S4U` физически стоят на разных строках. Без склейки
    привязать значение к ВЫЗОВУ невозможно, а без привязки сторож ловит
    слово, а не механизм."""
    return _JOIN_CONTINUATION.sub(" ", _registrar_code())


# Каждая пара — значение, привязанное к СВОЕМУ ВЫЗОВУ, а не просто стоящее
# рядом с параметром.
#
# 🔴 ПОЧЕМУ ПРИВЯЗКА, А НЕ СОСЕДСТВО. Слово «S4U» живёт в регистраторе ДВАЖДЫ:
# в вызове `New-ScheduledTaskPrincipal` и в финальном `Write-Host "... (S4U /
# Highest, AtStartup + AtLogOn) ..."`. Write-Host — это КОД, и `_ps_code` его
# не снимает. Сегодня искать `-LogonType\s+S4U` спасало только то, что в
# сообщении человеку нет подстроки `-LogonType`. Это совпадение, а не
# механизм: допишут в сообщение подсказку «-LogonType S4U» — и сторож станет
# зелёным навсегда, при любой мутации самого вызова.
SURVIVAL_SETTINGS = [
    (r"New-ScheduledTaskPrincipal.*?-LogonType\s+S4U(?!\w)",
     "задача не S4U: перестанет работать без залогиненного пользователя"),
    (r"New-ScheduledTaskPrincipal.*?-RunLevel\s+Highest(?!\w)",
     "задача не Highest: не хватит прав поднять панель"),
    (r"New-ScheduledTaskTrigger\s+-AtStartup(?!\w)",
     "нет триггера AtStartup: после ребута присмотра не будет"),
    (r"New-ScheduledTaskTrigger\s+-AtLogOn(?!\w)",
     "нет триггера AtLogOn: панель не вернётся после входа в систему"),
    (r"New-ScheduledTaskSettingsSet.*?-MultipleInstances\s+IgnoreNew(?!\w)",
     "задача может запуститься вторым экземпляром"),
]


@pytest.mark.parametrize("pattern,why", SURVIVAL_SETTINGS,
                         ids=[w.split(":")[0] for _p, w in SURVIVAL_SETTINGS])
def test_the_task_survives_logoff_and_reboot_like_its_three_neighbours(pattern, why):
    """S4U / Highest / IgnoreNew, триггеры AtStartup + AtLogOn (§2).

    Панель умерла ровно от того, что жила в интерактивной сессии. Задача без
    S4U повторила бы это через один разлогин.

    Ищем значение, привязанное к СВОЕМУ ВЫЗОВУ, и только в коде — см.
    `_registrar_code` и `_registrar_statements`.
    """
    assert re.search(pattern, _registrar_statements(), re.I), why


def test_the_task_registration_is_actually_called():
    """ГРАНИЦА: все настройки выше можно собрать правильно и не позвать
    `Register-ScheduledTask` — файл выглядит как регистратор и не
    регистрирует ничего."""
    code = _registrar_statements()
    call = re.search(r"Register-ScheduledTask\b.*", code)
    assert call, "регистратор ничего не регистрирует: %s" % code[:400]
    for flag in ("-TaskName", "-Action", "-Trigger", "-Principal", "-Settings"):
        assert flag in call.group(0), (
            "в Register-ScheduledTask не передан %s — настройка собрана и "
            "выброшена: %r" % (flag, call.group(0)))


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


# =====================================================================
# ДЫРЫ A-D: то, что мутационный гейт назвал непокрытым.
#
# Общая болезнь всех сторожей выше: они смотрели на ТЕКСТ скрипта или звали
# ЧИСТЫЕ функции. Между «функция правильная» и «её правильно зовут» лежит
# ровно тот зазор, в котором живут самые дорогие дефекты: мутация, меняющая
# `no_bind_address` на `no_response` в живом вердикте, не трогает ни одну
# чистую функцию и не убирает ни одного слова из файла — а реализует §2.3
# наизнанку и убивает ЗДОРОВУЮ панель.
#
# Приём один и тот же: дот-сорсим настоящий .ps1 с -NoLoop, ПОДМЕНЯЕМ
# измерители своими функциями (PowerShell разрешает переопределить функцию в
# том же скоупе после дот-сорса, и вызов по имени разрешается в момент
# вызова) и смотрим на ПОВЕДЕНИЕ. Настоящих сети, портов и процессов здесь
# нет — их подменять и надо, предмет проверки не они.
# =====================================================================

_OVERRIDE_MEASURERS = """
function Test-PanelPort { param([int]$PanelPort) return $%(listening)s }
function Get-PanelHealthStatus {
    param([string]$PanelHost, [int]$PanelPort, [int]$TimeoutSec = 5)
    return %(health)d
}
"""


def _reason(root, tailnet="", explicit="", bind="", listening=False, health=0,
            port=8011):
    """Вердикт ЖИВОГО цикла при заданном состоянии мира."""
    body = (_OVERRIDE_MEASURERS % {"listening": str(listening).lower(),
                                   "health": health}
            + "Get-PanelReason -TailnetIp '%s' -ExplicitHost '%s' -BindHost '%s' "
              "-PanelPort %d" % (tailnet, explicit, bind, port))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines, ("Get-PanelReason ничего не вернула: %r / %r"
                   % (run.stdout, run.stderr))
    return lines[-1]


# ---------------- ДЫРА A: вердикт живого цикла ----------------

def test_a_tailnet_fallback_to_the_loopback_is_never_called_dead(tmp_path):
    """🔴 САМАЯ ДОРОГАЯ. §2.3 целиком.

    Тайнет пропал. `resolve_client_host` НЕ отказывает — он молча отдаёт
    петлю. На петле панели нет никогда, поэтому порт свободен и /health
    молчит. Наивный вердикт — `no_response`, и следом гардиан снимает
    владельца порта и поднимает панель заново. Панель при этом ЖИВА на
    тайнетовом адресе: убито здоровое, по собственной слепоте.

    Разница между `no_bind_address` и `no_response` здесь — это разница
    между «жду» и «убиваю»."""
    assert _reason(tmp_path, tailnet="", explicit="", bind="127.0.0.1",
                   listening=False, health=0) == "no_bind_address"


def test_the_address_gate_runs_before_the_port_is_even_looked_at(tmp_path):
    """Порядок проверок — часть требования, а не стиль.

    Даже если на петле кто-то СЛУШАЕТ и отвечает 200, это не наша панель:
    адрес получен фолбэком, значит мы не знаем, где панель. Вердикт по
    чужому процессу был бы хуже молчания."""
    assert _reason(tmp_path, tailnet="", explicit="", bind="127.0.0.1",
                   listening=True, health=200) == "no_bind_address"


def test_a_deliberate_loopback_is_measured_like_any_other_address(tmp_path):
    """ГРАНИЦА. Запрет обязан касаться ФОЛБЭКА, а не петли как таковой:
    петля, выбранная человеком через PANEL_CLIENT_HOST, — законный адрес.

    Иначе §2.3 выключил бы измерение насовсем, и десятая проверка стала бы
    вечным `no_bind_address`, не замечающим ни одной настоящей смерти."""
    assert _reason(tmp_path, tailnet="", explicit="127.0.0.1",
                   bind="127.0.0.1", listening=True, health=200) == "ok"


def test_no_address_at_all_is_no_bind_address(tmp_path):
    """Резолвер отказал (сегодня это 0.0.0.0). Мерить нечего."""
    assert _reason(tmp_path, tailnet="100.102.179.47", bind="",
                   listening=True, health=200) == "no_bind_address"


def test_a_healthy_panel_on_the_tailnet_reads_as_ok(tmp_path):
    assert _reason(tmp_path, tailnet="100.102.179.47", bind="100.102.179.47",
                   listening=True, health=200) == "ok"


def test_a_free_port_reads_as_no_response(tmp_path):
    """Панель мертва, порт никем не занят — единственный случай, в котором
    гардиан обязан поднимать."""
    assert _reason(tmp_path, tailnet="100.102.179.47", bind="100.102.179.47",
                   listening=False, health=0) == "no_response"


def test_a_wedged_panel_reads_as_its_http_code(tmp_path):
    """Живая-но-больная отличается от мёртвой словом вердикта — и это
    единственное, что не даёт чинить не то."""
    assert _reason(tmp_path, tailnet="100.102.179.47", bind="100.102.179.47",
                   listening=True, health=500) == "http:500"


def test_a_listening_but_silent_panel_reads_as_no_response(tmp_path):
    """Порт держит, /health не ответил вовсе. У пробы §3 это `no_response`,
    и расходиться с ней словами нельзя: два сторожа с разными вокабулярами
    начнут спорить о том, кто DOWN."""
    assert _reason(tmp_path, tailnet="100.102.179.47", bind="100.102.179.47",
                   listening=True, health=0) == "no_response"


def test_the_verdict_refuses_to_invent_a_port(tmp_path):
    """Порт обязан ПРИХОДИТЬ. Умолчание внутри функции — вторая копия числа,
    переживающая правку параметра: порт сменили в одном месте, а вердикт
    выносится о старом. Молча."""
    body = (_OVERRIDE_MEASURERS % {"listening": "false", "health": 0}
            + "try { Get-PanelReason -TailnetIp '100.1.1.1' -BindHost '100.1.1.1'; "
              "'NO-THROW' } catch { 'THREW' }")
    run = _run_ps(body, tmp_path)
    out = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert out and out[-1] == "THREW", (
        "вердикт вынесен без порта — значит у функции есть своё мнение о "
        "нём: %r / %r" % (run.stdout, run.stderr))


# ---------------- ДЫРА B: ворота адреса, отдельно от вердикта ------------

def _measurable(root, tailnet="", explicit="", bind="") -> bool:
    body = ("Test-PanelAddressMeasurable -TailnetIp '%s' -ExplicitHost '%s' "
            "-BindHost '%s'" % (tailnet, explicit, bind))
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines and lines[-1] in ("True", "False"), (run.stdout, run.stderr)
    return lines[-1] == "True"


def test_an_empty_bind_address_is_not_measurable(tmp_path):
    assert _measurable(tmp_path, tailnet="100.1.1.1", bind="") is False


def test_a_silent_fallback_is_not_measurable(tmp_path):
    """🔴 Инверсия этой единственной строки возвращает петлю-фолбэк, ради
    запрета которой §2.3 и написан."""
    assert _measurable(tmp_path, tailnet="", explicit="",
                       bind="127.0.0.1") is False


def test_an_explicit_host_is_measurable_even_without_a_tailnet(tmp_path):
    """Человек назвал адрес вслух — там и мерим. Иначе PANEL_CLIENT_HOST
    перестал бы работать как способ поднять панель на петле."""
    assert _measurable(tmp_path, tailnet="", explicit="127.0.0.1",
                       bind="127.0.0.1") is True


def test_a_live_tailnet_is_measurable(tmp_path):
    assert _measurable(tmp_path, tailnet="100.102.179.47",
                       bind="100.102.179.47") is True


# ---------------- ДЫРА D: освобождение порта и разбор ответа -------------

def test_the_launch_frees_the_port_first_and_gives_up_if_it_cannot(tmp_path):
    """🔴 Убрать освобождение порта — и зависший uvicorn держит 8011 ВЕЧНО,
    а новая панель не встаёт НИКОГДА.

    Статический сторож этого не видит: `Get-NetTCPConnection`/`OwningProcess`
    остаются в файле, просто их больше никто не зовёт перед подъёмом.

    Подменяем освобождение на «не смог» и требуем, чтобы подъём НЕ
    состоялся. Если вызов убрали, Start-Panel пойдёт запускать процесс и
    вернёт что угодно, кроме отказа."""
    body = ("$script:CALLED = $false\n"
            "function Stop-PanelPortOwner { param([int]$PanelPort, [int]$MaxWaitSec = 10) "
            "$script:CALLED = $true; return $false }\n"
            "$r = Start-Panel -PanelHost '100.1.1.1' -PanelPort 8011 -PanelSlug 'yarina' -ReadySec 1\n"
            "'CALLED={0} RESULT={1}' -f $script:CALLED, $r")
    run = _run_ps(body, tmp_path, timeout=90)
    out = " ".join(ln.strip() for ln in run.stdout.splitlines() if ln.strip())
    assert "CALLED=True" in out, (
        "подъём не спросил освобождение порта: %r / %r" % (run.stdout, run.stderr))
    assert "RESULT=busy" in out, (
        "порт освободить не удалось, а подъём всё равно пошёл: %r" % (out,))


def _health_server(tmp_path, status: int):
    """Живой HTTP-сервер, отвечающий заданным кодом на /health."""
    script = tmp_path / ("srv_%d.py" % status)
    script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "CODE = int(sys.argv[1])\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        body = b'{\"ok\": true}' if CODE == 200 else b'boom'\n"
        "        self.send_response(CODE)\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "srv = HTTPServer(('127.0.0.1', 0), H)\n"
        "print(srv.server_address[1], flush=True)\n"
        "srv.serve_forever()\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(status)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    port = int(proc.stdout.readline().strip())
    return proc, port


def _health(root, host, port) -> int:
    body = "Get-PanelHealthStatus -PanelHost '%s' -PanelPort %d -TimeoutSec 5" % (host, port)
    run = _run_ps(body, root)
    assert run.returncode == 0, (run.stdout, run.stderr)
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines, (run.stdout, run.stderr)
    return int(lines[-1])


def test_a_live_health_endpoint_reports_its_code(tmp_path):
    proc, port = _health_server(tmp_path, 200)
    try:
        assert _health(tmp_path, "127.0.0.1", port) == 200
    finally:
        proc.kill()


def test_a_five_hundred_is_not_flattened_into_no_answer(tmp_path):
    """🔴 В PowerShell 5.1 ответ 4xx/5xx прилетает ИСКЛЮЧЕНИЕМ, а не
    значением. Выбросить разбор `$_.Exception.Response` — и живая-но-больная
    панель станет неотличима от мёртвой: её начнут ПЕРЕЗАПУСКАТЬ вместо того,
    чтобы чинить код внутри, и так по кругу.

    Сервер поднимается настоящий: подделка тут доказывала бы только саму
    себя — предмет проверки в том, как PowerShell отдаёт неуспешный ответ."""
    proc, port = _health_server(tmp_path, 500)
    try:
        assert _health(tmp_path, "127.0.0.1", port) == 500
    finally:
        proc.kill()


def test_nobody_listening_reports_zero_not_a_code(tmp_path):
    """Свободный порт — это 0, сентинел «ответа не было вовсе». Любой
    настоящий код здесь означал бы, что мы приняли отсутствие ответа за
    ответ."""
    proc, port = _health_server(tmp_path, 200)
    proc.kill()
    proc.wait(timeout=10)
    assert _health(tmp_path, "127.0.0.1", port) == 0


def test_an_empty_host_is_not_asked_at_all(tmp_path):
    assert _health(tmp_path, "", 8011) == 0


# ---------------- ДЫРА C: ПРОВОДКА ЦИКЛА ----------------
#
# Всё, что выше, проверяет функции. Цикл `while ($true)` не проверял НИКТО:
# сторожа дот-сорсят файл с -NoLoop, а тело цикла живёт под `if (-not
# $NoLoop)` и в бесконечном цикле — позвать его нельзя.
#
# Зазор не теоретический. Все четыре мутации ниже оставляют ВСЕ чистые
# функции идеально правильными:
#   * в ветке «не могу измерить» позвать Step-RefusalState с 'refused'
#     вместо 'unmeasurable' — пропавший тайнет копит отказы, С10 обойдён;
#   * не читать $st.Alert — Step-RefusalState честно его возвращает, а
#     строка о входе в состояние не появляется НИКОГДА;
#   * не звать Format-RefusalLine — §2.2 не выполнен, функция зелёная;
#   * не запоминать момент отказа — пауза не наступает, перезапуск раз в 15 с.
#
# Требование, которое эти сторожа предъявляют: ОДНА ИТЕРАЦИЯ обязана быть
# вызываемой снаружи. `while ($true) { $s = Invoke-PanelGuardianCycle ...;
# Start-Sleep }` — форма, при которой проводка перестаёт быть слепой зоной.
# Сон остаётся в цикле: шаг не спит, иначе его нельзя прогнать быстро.

CYCLE_CONTRACT = """
$script:LINES = @()
$script:STARTED = $false
function Write-G { param([string]$msg) $script:LINES += ($msg + '') }
function Get-PanelBind {
    return [pscustomobject]@{ TailnetIp = '100.102.179.47'; ExplicitHost = '';
                              BindHost = '100.102.179.47'; Failed = $false }
}
function Get-PanelReason {
    param([string]$TailnetIp = '', [string]$ExplicitHost = '',
          [string]$BindHost = '', [int]$PanelPort = 0)
    return '%(reason)s'
}
function Start-Panel {
    param([string]$PanelHost, [int]$PanelPort, [string]$PanelSlug, [int]$ReadySec = 45)
    $script:STARTED = $true
    return '%(outcome)s'
}
function Get-RefusalReason { param([int]$MaxLines = 20) return '%(refusal)s' }
$r = Invoke-PanelGuardianCycle -Refusals %(refusals)d -Alerted $%(alerted)s `
     -LastReason '%(last_reason)s' -SinceRefusalSec %(since)d
'STATE|{0}|{1}|{2}|{3}|{4}' -f $r.Refusals, $r.Alerted, $r.SinceRefusalSec, `
     $script:STARTED, $r.LastReason
$script:LINES | ForEach-Object { 'LOG|' + $_ }
"""

REFUSAL_TEXT = "TAMAPI_DB не задан -- уникальная причина этого прогона"


def _cycle(root, reason, outcome="refused", refusals=0, alerted=False,
           last_reason="", since=2147483647, refusal=REFUSAL_TEXT):
    """Одна итерация живого цикла с подменёнными измерителями."""
    body = CYCLE_CONTRACT % {
        "reason": reason, "outcome": outcome, "refusals": refusals,
        "alerted": str(alerted).lower(), "last_reason": last_reason,
        "since": since, "refusal": refusal}
    run = _run_ps(body, root, timeout=90)
    assert run.returncode == 0, (run.stdout, run.stderr)
    state, logs = None, []
    for line in run.stdout.splitlines():
        line = line.strip()
        if line.startswith("STATE|"):
            _tag, refs, alert, sincev, started, lastr = line.split("|", 5)
            state = {"refusals": int(refs), "alerted": alert == "True",
                     "since": int(sincev), "started": started == "True",
                     "last_reason": lastr}
        elif line.startswith("LOG|"):
            logs.append(line[4:])
    assert state is not None, (
        "Invoke-PanelGuardianCycle не вернула состояние — тело цикла нельзя "
        "прогнать снаружи, и вся проводка остаётся слепой зоной: %r / %r"
        % (run.stdout, run.stderr))
    state["logs"] = logs
    return state


def test_one_iteration_of_the_live_loop_can_be_run_from_outside(tmp_path):
    """Предпосылка всех сторожей ниже — и требование само по себе.

    Пока итерация не вызывается, «функция правильная» и «её правильно зовут»
    неразличимы, а живут в этом зазоре самые дорогие дефекты."""
    st = _cycle(tmp_path, reason="ok", outcome="alive")
    assert st["started"] is False, "здоровую панель поднимали заново"
    assert st["refusals"] == 0, st


def test_the_loop_does_not_count_an_unmeasurable_cycle_as_a_refusal(tmp_path):
    """🔴 Мутация 'unmeasurable' -> 'refused' в ветке §2.3.

    Чистая Step-RefusalState остаётся правильной, С10 на ней зелёный — а
    пропавший на час тайнет сам доводит гардиана до состояния исчерпанных
    отказов и печатает строку о том, чего не было."""
    st = _cycle(tmp_path, reason="no_bind_address", refusals=2,
                last_reason="no_response")
    assert st["refusals"] == 2, (
        "цикл «не могу измерить» потратил счётчик отказов: %s" % st)
    assert st["started"] is False, "на no_bind_address подняли панель"


def test_the_loop_says_it_once_on_entry_and_stays_quiet_after(tmp_path):
    """Вход в «не могу измерить» назван, повтор — нет (§2.3). Ежецикловая
    запись раз в 15 с за ночь даёт 2880 строк."""
    entry = _cycle(tmp_path, reason="no_bind_address", last_reason="ok")
    assert entry["logs"], "вход в no_bind_address прошёл молча"
    again = _cycle(tmp_path, reason="no_bind_address",
                   last_reason="no_bind_address")
    assert not again["logs"], (
        "цикл пишет про адрес каждый раз: %s" % (again["logs"],))


def test_the_loop_says_it_again_when_the_address_comes_back(tmp_path):
    """Выход из состояния обязан быть в журнале: одна только запись о входе
    делает «тайнет пропадал на ночь» неотличимым от «тайнета нет до сих
    пор»."""
    st = _cycle(tmp_path, reason="ok", outcome="alive",
                last_reason="no_bind_address")
    assert st["logs"], "возврат адреса прошёл молча"


def test_the_loop_consults_the_decision_before_starting(tmp_path):
    """Пауза после отказа обязана СОБЛЮДАТЬСЯ циклом, а не только считаться
    чистой функцией. Иначе перезапуск раз в 15 секунд."""
    st = _cycle(tmp_path, reason="no_response", refusals=1, since=10)
    assert st["started"] is False, (
        "цикл поднял панель, не дождавшись паузы после отказа: %s" % st)


def test_the_loop_writes_the_reason_on_every_single_attempt(tmp_path):
    """🔴 Мутация «убрать Write-G (Format-RefusalLine ...)».

    Причина обязана попасть в журнал НА КАЖДОЙ попытке (§2.2) — иначе через
    час не отличить «всё та же ошибка» от «уже другая». Проверяем по тексту,
    который вернул stdout панели: константа его не содержит."""
    first = _cycle(tmp_path, reason="no_response", refusals=0, since=0)
    assert any(REFUSAL_TEXT in ln for ln in first["logs"]), (
        "причина отказа не доехала до журнала: %s" % (first["logs"],))
    second = _cycle(tmp_path, reason="no_response", refusals=1, since=99999)
    assert any(REFUSAL_TEXT in ln for ln in second["logs"]), (
        "вторая попытка следа не оставила — «на каждой попытке» не выполнено")


def test_the_loop_announces_the_exhausted_state_exactly_once(tmp_path):
    """🔴 Мутация «не читать $st.Alert».

    Step-RefusalState честно возвращает Alert, сторожа С8 на ней зелёные — а
    строка о входе в состояние не появляется НИКОГДА.

    Считаем строки, НЕ являющиеся обычной строкой отказа: на входе их
    больше нуля, дальше — ноль. Сравнение по смыслу, а не по тексту: сторож
    на дословную формулировку сломался бы от правки слов."""
    # Оба прогона отличаются ТОЛЬКО состоянием: вердикт один и тот же, и
    # `last_reason` совпадает с ним, чтобы строка о смене вердикта не
    # появилась ни в одном из них и не изобразила собой алерт. Различие
    # обязано дать РОВНО одна вещь — вход в состояние.
    entry = _cycle(tmp_path, reason="no_response", refusals=2,
                   alerted=False, since=99999, last_reason="no_response")
    extra = [ln for ln in entry["logs"] if REFUSAL_TEXT not in ln]
    assert entry["alerted"] is True, entry
    assert extra, (
        "вход в состояние исчерпанных отказов прошёл молча: %s" % (entry["logs"],))

    later = _cycle(tmp_path, reason="no_response", refusals=5, alerted=True,
                   since=99999, last_reason="no_response")
    extra_later = [ln for ln in later["logs"] if REFUSAL_TEXT not in ln]
    assert not extra_later, (
        "состояние объявляется повторно: %s" % (extra_later,))


def test_the_loop_remembers_when_the_refusal_happened(tmp_path):
    """🔴 Мутация «не запоминать момент отказа».

    Без отметки времени пауза не наступает никогда: `$since` навсегда
    остаётся максимумом, и `Test-ShouldStartPanel` честно разрешает попытку
    КАЖДЫЕ 15 СЕКУНД. Чистая функция при этом идеально правильная — она же
    получает то, что ей дали."""
    st = _cycle(tmp_path, reason="no_response", refusals=0, since=2147483647)
    assert st["since"] < 300, (
        "после отказа часы не пошли (since=%s) — паузы не будет никогда, "
        "перезапуск раз в интервал" % st["since"])


def test_a_successful_start_clears_the_state_in_the_loop(tmp_path):
    """Парный к С8 на уровне ПРОВОДКИ: обнуление живёт в цикле, а не только
    в чистой функции."""
    st = _cycle(tmp_path, reason="no_response", outcome="alive", refusals=5,
                alerted=True, since=99999)
    assert st["started"] is True, st
    assert st["refusals"] == 0, "счётчик пережил успешный подъём: %s" % st
    assert st["alerted"] is False, "признак «уже отмечали» пережил подъём: %s" % st


def test_the_endless_loop_is_a_thin_caller_of_that_one_step():
    """Инвариант, из которого следует всё остальное: рассуждение живёт в
    вызываемой функции, а `while ($true)` только зовёт её и спит.

    Иначе завтра логику допишут прямо в цикле, и слепая зона вернётся."""
    code = _guardian_code()
    assert re.search(r"function\s+Invoke-PanelGuardianCycle", code, re.I), (
        "шага цикла не существует — тело `while ($true)` снаружи не позвать, "
        "и вся проводка остаётся непроверяемой")
    loop = code[code.index("while"):] if "while" in code else ""
    assert re.search(r"Invoke-PanelGuardianCycle", loop, re.I), (
        "бесконечный цикл не зовёт шаг — значит рассуждение живёт в нём же")
