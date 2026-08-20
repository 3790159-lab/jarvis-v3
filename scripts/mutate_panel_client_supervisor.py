"""Мутационный гейт арки «присмотр за клиентской панелью :8011».

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md.
Сторожа: tests/test_ops_watchdog_panel_client.py, tests/test_panel_client_health.py,
tests/test_panel_client_guardian.py, tests/test_panel_client_port_one_number.py.

ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ВРЕЗКА В `mutate_ops_watchdog.py`. Три причины, и каждая
одна была бы достаточной:

* предмет другой. Тот гейт охраняет КАНАЛ АЛЕРТА и журнал одного файла; этот —
  РЕШЕНИЕ «поднимать или не поднимать живую панель», разложенное по трём
  файлам на ДВУХ языках (`scripts/ops_watchdog.py`, `app/panel_client.py`,
  `scripts/panel_client_guardian_detached.ps1`);
* механика записи другая. `.ps1` обязан лежать на диске С BOM (без него PS 5.1
  читает файл как cp1251 и падает не на мутации, а на кодировке — это ложный
  красный, неотличимый от пойманной мутации), `.py` — БЕЗ BOM. Один writer на
  оба случая в образце не предусмотрен, и дописывать его в чужой гейт значит
  менять поведение ста уже работающих мутаций;
* окно совпадения байткода. В `mutate_ops_watchdog.py` все ~100 мутаций бьют
  в ОДИН файл — там это окно самое широкое из всех гейтов, и шапка того файла
  об этом прямо предупреждает. Добавлять туда ещё десятки записей в тот же
  файл — увеличивать ровно ту дыру, которую там чинили.

ЧЕТЫРЕ СПОСОБА, КОТОРЫМИ ТАКОЙ ГЕЙТ ВРЁТ. Закрыты все четыре, и ни один —
рассуждением: каждый уже случался.

1. ЧУЖОЙ БАЙТКОД (DEV-26). Python признаёт кэш `.pyc` актуальным по паре
   (mtime в ЦЕЛЫХ секундах, размер исходника). Две мутации одинакового
   размера, записанные в одну секунду, для кэша неотличимы — и вторая
   исполняется байткодом первой, а гейт печатает `[ok]`. Лечится подписью:
   `write_mutant` выдаёт каждой записи СВОЙ mtime.

2. НЕНУЛЕВОЙ rc, ПРИНЯТЫЙ ЗА КРАСНОТУ. Мутация, сломавшая СБОР тестов,
   отвечает rc 2 / 4 / 5, и «любой ненулевой rc = поймана» печатает её как
   `[ok]`. Красным считается РОВНО `rc 1` ПЛЮС слово `failed` в выводе.

3. КОДИРОВКА. `text=True` берёт `locale.getpreferredencoding()` = cp1251, а в
   cp1251 байт `0x98` НЕ ОПРЕДЕЛЁН; в UTF-8 он приходит из «И». Одна заглавная
   «И» в тексте упавшего ассерта — и поток subprocess падает
   `UnicodeDecodeError`, вывод приходит ПУСТЫМ, `failed` в нём не находится, и
   КАЖДАЯ пойманная мутация печатается слепой (замерено: «38 слепых из 100» на
   краснеющих сторожах). Кодировка чтения задана ЯВНО. Ассерты этой арки —
   русские и украинские, так что ловушка здесь не гипотетическая.

4. МИШЕНЬ УЕЗЖАЕТ ОТ КОДА. Строка, которую ищет мутация, со временем
   перестаёт существовать в файле; `str.replace` молча не применяется, гейт
   исполняет НЕИЗМЕНЁННЫЙ код и печатает `[ok]` — то есть отчитывается за
   мутацию, которой не было. Лечится СТАТИЧЕСКОЙ СВЕРКОЙ (`--check`): каждая
   искомая строка обязана встречаться в целевом файле РОВНО ОДИН РАЗ, и
   сверка гоняется ПЕРЕД первой мутацией, а не вместо неё. «Ровно один», а не
   «хотя бы один»: два вхождения означают, что `replace(..., 1)` попал в
   первое, и какое именно место мутировано — неизвестно.

   Сверяется и ВТОРАЯ половина мишени — ИМЯ ТЕСТА: переименованный сторож
   даёт «collected 0 items» и rc 4, то есть по критерию §2 честно печатается
   слепым, но причина («сторожа нет») утонула бы в сотне строк. Статическая
   сверка называет её словами и до прогона.

Предохранители те же, что у образца: `refuse_if_live_tree` (DEV-31 — гейт
мутирует ТОЛЬКО worktree, потому что гардиан поднимает прод из живого дерева
и совпадение с окном мутации уехало бы в прод) и отказ работать на грязном
дереве (откат идёт `git checkout --`, то есть сотрёт незакоммиченное).

ЗАПУСКАТЬ ТОЛЬКО ИЗ PowerShell:
    C:/jarvis/.venv/Scripts/python.exe scripts/mutate_panel_client_supervisor.py --check
    C:/jarvis/.venv/Scripts/python.exe scripts/mutate_panel_client_supervisor.py
"""
from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись (см. §1 шапки).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

# ── что мутируем ────────────────────────────────────────────────────────────
W = "scripts/ops_watchdog.py"                        # проба (десятая)
A = "app/panel_client.py"                            # /health и состав ручек
G = "scripts/panel_client_guardian_detached.ps1"     # присмотр
R = "scripts/register_panel_client_guardian.ps1"     # задача планировщика

# ── чем ловим ───────────────────────────────────────────────────────────────
TP = "tests/test_ops_watchdog_panel_client.py"
TH = "tests/test_panel_client_health.py"
TG = "tests/test_panel_client_guardian.py"
TN = "tests/test_panel_client_port_one_number.py"
TL = "tests/test_panel_client_guardian_live.py"      # ЖИВОЙ проход процессом

# (имя, файл, что заменить, на что, какой тест ОБЯЗАН покраснеть)
#
# `что заменить` / `на что` — строка ИЛИ список строк равной длины: одна
# правдоподобная правка иногда трогает два места сразу (см. приманку
# регистратора, где значение меняют в вызове и «чинят» слово в сообщении
# человеку). Разбивать такую правку на две мутации значило бы проверять не то,
# что человек сделает одним движением.
#
# Каждая мутация — ПРАВДОПОДОБНАЯ будущая правка: то, что человек напишет,
# упрощая код, «прибирая дубли» или выполняя чужую просьбу, — а не случайный
# мусор. Мусор ловится чем угодно и не доказывает ничего.
MUTATIONS = [
    # ── §2.3: «не могу измерить» ≠ «мертва» ────────────────────────────────
    # Класс дефекта, ценой которого будет УБИТАЯ ЗДОРОВАЯ панель: тайнет
    # пропал -> проба резолвит петлю -> «не отвечает» -> гардиан сносит
    # владельца порта. Ровно то, что стоило 13 ч 42 мин простоя 16.08.
    ("проба: no_bind_address схлопнут в no_response — «не смогли спросить» "
     "выдано за «мертва»", W,
     '        return {"ok": False, "reason": "no_bind_address", "detail": detail}',
     '        return {"ok": False, "reason": "no_response", "detail": detail}',
     TP + "::test_the_unresolvable_address_is_not_confused_with_a_dead_port"),

    ("снимок: пропавший тайнет меряется на петле — фолбэк резолвера принят "
     "за адрес панели", W,
     "    if host and not explicit and not ip:",
     "    if False:",
     TP + "::test_a_vanished_tailnet_is_not_measured_on_the_loopback"),

    ("снимок: правило §2.3 расползлось — тайнет на месте, а мерить всё равно "
     "нечем (вечный no_bind_address)", W,
     "    if host and not explicit and not ip:",
     "    if host and not explicit:",
     TP + "::test_a_present_tailnet_is_still_measured_normally"),

    ("гардиан ПЕРЕЗАПУСКАЕТ панель на вердикте no_bind_address (§2.3)", G,
     "    if ($Reason -eq 'no_bind_address') { return $false }",
     "    if ($false) { return $false }",
     TG + "::test_an_unmeasurable_address_never_starts_anything"),

    ("гардиан поднимает поверх ЖИВОЙ: вердикт `ok` перестал быть запретом", G,
     "    if ($Reason -eq 'ok') { return $false }",
     "    if ($false) { return $false }",
     TG + "::test_a_live_panel_is_left_alone"),

    # ── §2.2: исчерпанные отказы ───────────────────────────────────────────
    ("граница backoff стала СТРОГОЙ вместо включительной (300 с — уже нет)", G,
     "    return ($SinceRefusalSec -ge $wait)",
     "    return ($SinceRefusalSec -gt $wait)",
     TG + "::test_the_backoff_boundary_is_inclusive"),

    ("длинный интервал §2.2 выкинут: после трёх отказов попытки прекращаются "
     "НАВСЕГДА (поведение, отменённое владельцем)", G,
     "    $wait = if ($Refusals -ge $MaxRefusals) { $LongRetrySeconds } else { $BackoffSeconds }\n"
     "    return ($SinceRefusalSec -ge $wait)",
     "    if ($Refusals -ge $MaxRefusals) { return $false }\n"
     "    return ($SinceRefusalSec -ge $BackoffSeconds)",
     TG + "::test_the_attempts_never_stop"),

    ("длинного интервала нет в другую сторону: после трёх отказов долбим "
     "каждые 300 с", G,
     "    $wait = if ($Refusals -ge $MaxRefusals) { $LongRetrySeconds } else { $BackoffSeconds }",
     "    $wait = $BackoffSeconds",
     TG + "::test_three_refusals_move_to_the_long_interval"),

    ("алерт о входе в состояние стал ПОВТОРЯЮЩИМСЯ: Alerted перестал быть "
     "флагом состояния", G,
     "            if (($next -ge $MaxRefusals) -and (-not $Alerted)) {",
     "            if ($next -ge $MaxRefusals) {",
     TG + "::test_the_tenth_and_the_hundredth_attempt_stay_silent"),

    ("успешный подъём перестал обнулять счётчик отказов", G,
     "        'ok' {\n            $next = 0\n            $nextAlerted = $false\n        }",
     "        'ok' {\n            $nextAlerted = $false\n        }",
     TG + "::test_a_successful_start_clears_the_state"),

    ("успешный подъём не снимает флаг алерта — следующий вход промолчит", G,
     "        'ok' {\n            $next = 0\n            $nextAlerted = $false\n        }",
     "        'ok' {\n            $next = 0\n        }",
     TG + "::test_the_next_entry_alerts_again"),

    ("«не могу измерить» тратит отказ: пропавший тайнет сам уводит панель в "
     "длинный интервал за чужую вину", G,
     "        'unmeasurable' {\n"
     "            # Намеренно ничего: попытки не было, судить не о чем.\n"
     "        }",
     "        'unmeasurable' {\n"
     "            $next = $Refusals + 1\n"
     "        }",
     TG + "::test_an_unmeasurable_cycle_does_not_spend_the_refusal_counter"),

    # ── §2.2: журнал причин ────────────────────────────────────────────────
    ("причина отказа в журнале заменена КОНСТАНТОЙ — «одна и та же ошибка "
     "или разные» больше не отличить", G,
     "    return ('ОТКАЗ СТАРТА (rc 1), попытка {0}: {1}' -f $Refusals, $text)",
     "    return ('ОТКАЗ СТАРТА (rc 1), попытка {0}: панель не поднялась' -f $Refusals)",
     TG + "::test_two_different_reasons_give_two_different_lines"),

    ("причина отказа обрезана — в отрезанном хвосте стоит имя переменной, "
     "которую надо задать", G,
     "    return ('ОТКАЗ СТАРТА (rc 1), попытка {0}: {1}' -f $Refusals, $text)",
     "    return ('ОТКАЗ СТАРТА (rc 1), попытка {0}: {1}' -f $Refusals,\n"
     "            $text.Substring(0, [Math]::Min(24, $text.Length)))",
     TG + "::test_the_reason_is_not_truncated_away"),

    # ── §2.3: журнал «не могу измерить» ────────────────────────────────────
    ("Test-ShouldLogUnmeasurable пишет КАЖДЫЙ ЦИКЛ: 2880 строк за ночь "
     "пропавшего тайнета", G,
     "    return ($WasUnmeasurable -ne $IsUnmeasurable)",
     "    return $IsUnmeasurable",
     TG + "::test_the_unmeasurable_state_is_not_logged_every_cycle"),

    ("выход из состояния не пишется: «тайнет пропадал на ночь» неотличимо от "
     "«тайнета нет до сих пор»", G,
     "    return ($WasUnmeasurable -ne $IsUnmeasurable)",
     "    return ((-not $WasUnmeasurable) -and $IsUnmeasurable)",
     TG + "::test_the_unmeasurable_state_is_logged_on_entry_and_on_exit"),

    # ── §2.1: DEV-38, освобождение порта ───────────────────────────────────
    # Класс, который стоил прода 18.08: pytest из worktree позвал тамошний
    # скрипт, тот снёс боевого по глобальной подстроке.
    ("освобождение порта переехало с OwningProcess на матч по подстроке "
     "командной строки (класс DEV-38)", G,
     "    foreach ($owner in (Get-PanelPortOwner -PanelPort $PanelPort)) {",
     "    foreach ($owner in @(Get-Process python -ErrorAction SilentlyContinue |\n"
     "            Where-Object { $_.CommandLine -match 'run_panel_client' } |\n"
     "            ForEach-Object { $_.Id })) {",
     TG + "::test_the_guardian_never_selects_processes_by_a_substring"),

    ("гардиан читает CommandLine, чтобы не убить САМ СЕБЯ — ловушка «запуск "
     "≠ упоминание»", G,
     "    return @($conns | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)",
     "    return @($conns | ForEach-Object { [int]$_.OwningProcess } |\n"
     "        Where-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).CommandLine -notmatch 'panel_client_guardian' } |\n"
     "        Sort-Object -Unique)",
     TG + "::test_the_guardian_does_not_look_for_itself"),

    ("PID-лок сверяет только НОМЕР: после kill номер переиспользуется, и "
     "живой гардиан не стартует никогда", G,
     "                if ($alive -and ($name -eq 'powershell' -or $name -eq 'pwsh')) {",
     "                if ($alive) {",
     TG + "::test_the_guardian_holds_a_pid_lock_per_slug"),

    # ── §3.1: адрес пробы — не 127.0.0.1 и не BASE_URL ─────────────────────
    ("проба вернулась на 127.0.0.1 вместо общего резолвера", W,
     "    try:\n"
     '        ip = (module.tailnet_ip() or "").strip()\n'
     '        explicit = (environ.get(module.HOST_VAR) or "").strip()\n'
     "        host, problem = module.resolve_client_host(None, ip=ip)",
     "    try:\n"
     '        ip = (module.tailnet_ip() or "").strip()\n'
     '        explicit = (environ.get(module.HOST_VAR) or "").strip()\n'
     '        host, problem = "127.0.0.1", None',
     TP + "::test_the_probe_address_moves_when_the_bind_resolver_moves"),

    ("проба ходит через BASE_URL бэкенда — это адрес ДРУГОГО процесса", W,
     '    url = "http://%s:%d%s" % (host, port, path)',
     "    url = BASE_URL + path",
     TP + "::test_the_probe_does_not_reuse_the_backend_base_url"),

    ("второй резолвер адреса рядом с первым — два числа на одну вещь", W,
     "def _panel_http_get(host: str, port: int, path: str):",
     "def _panel_bind_host_again():\n"
     "    m = _load_run_panel_client()\n"
     "    return m.resolve_client_host(None, ip=m.tailnet_ip())[0]\n"
     "\n"
     "\n"
     "def _panel_http_get(host: str, port: int, path: str):",
     TP + "::test_the_address_is_computed_in_exactly_one_place"),

    ("гардиан зашил адрес: /health спрашивается на петле, где панели нет", G,
     "    $url = 'http://{0}:{1}/health' -f $PanelHost, $PanelPort",
     "    $url = 'http://127.0.0.1:{0}/health' -f $PanelPort",
     TG + "::test_the_guardian_hardcodes_no_address"),

    # ── §3: состав проб ────────────────────────────────────────────────────
    ("состав проб вернулся к ДЕВЯТИ — десятую забыли подключить", W,
     "    if panel_client_snapshot:\n"
     '        probes["panel_client"] = probe_panel_client(panel_client_snapshot)',
     "    if False:\n"
     '        probes["panel_client"] = probe_panel_client(panel_client_snapshot)',
     TP + "::test_the_cycle_runs_exactly_these_ten_checks"),

    ("проба приезжает БЕЗ снимка: watchdog шлёт DOWN о том, чего не мерил", W,
     "    if panel_client_snapshot:",
     "    if True:",
     TP + "::test_without_a_snapshot_the_panel_check_does_not_appear_out_of_nowhere"),

    ("проба: «жива, но отвечает 500» выдана за здоровую", W,
     '    return {"ok": status == 200,',
     '    return {"ok": status is not None,',
     TP + "::test_a_live_process_answering_badly_is_a_different_reason"),

    ("проба: адрес заехал в reason — каждый переезд тайнета читается как "
     "НОВАЯ авария", W,
     '        return {"ok": False, "reason": "no_response",',
     '        return {"ok": False, "reason": "no_response:%s" % where,',
     TP + "::test_the_reason_is_stable_while_the_detail_moves"),

    ("ярлык пробы потерял порт — владелец пойдёт чинить :8010, у которой "
     "свой гардиан", W,
     '    "panel_client": "ПАНЕЛЬ КЛИЕНТА (:8011 /health)",',
     '    "panel_client": "ПАНЕЛЬ КЛИЕНТА (/health)",',
     TP + "::test_the_check_has_a_human_label"),

    # ── §3B: /health на клиентском приложении ──────────────────────────────
    ("/health начал требовать ключ — у watchdog'а ключа клиента нет и не "
     "будет", A,
     '    @api.get("/health")',
     "    from fastapi import Depends\n"
     "    from app.routers.panels_auth import require_owner\n"
     "\n"
     '    @api.get("/health", dependencies=[Depends(require_owner)])',
     TH + "::test_health_answers_without_any_key"),

    ("/health отдаёт слаг клиента «для удобства диагностики»", A,
     '        return {"ok": True}',
     "        import os\n"
     '        return {"ok": True, "slug": os.environ.get("TAMAPI_SLUG", "")}',
     TH + "::test_health_leaks_nothing_about_the_client"),

    # 🔴 НАХОДКА ПЕРВОГО ПОЛНОГО ПРОГОНА. Мутация ниже утекает путь к базе
    # НАСТОЯЩИМ (виндовым) видом — ровно так, как он лежит в `TAMAPI_DB`, — и
    # адресный сторож `test_health_does_not_leak_the_database_path` остаётся
    # ЗЕЛЁНЫМ. Причина замерена, а не выведена: тело ответа — JSON, а JSON
    # УДВАИВАЕТ каждую обратную косую. Путь в теле есть, но его написание в
    # теле и в переменной РАЗНОЕ:
    #
    #     db   = C:\Users\...\yarina.db
    #     body = {"ok": true, "db": "C:\\Users\\...\\yarina.db"}
    #
    # Оба утверждения сторожа (`db not in body` и его же со слэшами вперёд)
    # промахиваются мимо этого написания, и промахиваются они по построению.
    # Ловит утечку только СОСЕДНИЙ параметризованный сторож — по имени файла
    # базы, — то есть адресный сторож на путь не охраняет ничего.
    #
    # Сторожа не выдумываю: мутация нацелена на того, кто её ДЕЙСТВИТЕЛЬНО
    # ловит, а дыра названа здесь и в отчёте.
    ("/health отдаёт путь к базе клиента виндовым видом (адресный сторож на "
     "путь этого НЕ ВИДИТ: JSON удваивает обратную косую)", A,
     '        return {"ok": True}',
     "        import os\n"
     '        return {"ok": True, "db": os.environ.get("TAMAPI_DB", "")}',
     TH + "::test_health_says_exactly_one_thing"),

    # ГРАНИЦА к предыдущей: то же самое, но со слэшами ВПЕРЁД. JSON их не
    # экранирует, написание совпадает — и адресный сторож краснеет. То есть
    # он охраняет ровно ту форму пути, которой в `TAMAPI_DB` на Windows не
    # бывает никогда.
    ("/health отдаёт путь к базе со слэшами вперёд — единственная форма, "
     "которую адресный сторож на путь умеет увидеть", A,
     '        return {"ok": True}',
     "        import os\n"
     '        return {"ok": True, "db": os.environ.get("TAMAPI_DB", "")'
     '.replace(os.sep, "/")}',
     TH + "::test_health_does_not_leak_the_database_path"),

    # ── §3B/С4: состав приложения ──────────────────────────────────────────
    ("/panel/jarvis приехал в КЛИЕНТСКОЕ приложение: ключ клиента открыл бы "
     "PID'ы, ветки и сроки ключей фермы", A,
     '    @api.get("/health")',
     '    @api.get("/panel/jarvis")\n'
     "    async def jarvis_panel() -> dict:\n"
     '        return {"ok": True}\n'
     "\n"
     '    @api.get("/health")',
     TH + "::test_the_jarvis_panel_is_still_absent"),

    ("вместе с /health приехал startup-обработчик — второй хозяин у живого "
     "бота (DEV-38)", A,
     '    @api.get("/health")',
     "    api.router.on_startup.append(lambda: None)\n"
     "\n"
     '    @api.get("/health")',
     TH + "::test_health_did_not_drag_in_background_tasks"),

    ("карта ручек приоткрылась ради новой пробы", A,
     "                  docs_url=None, redoc_url=None, openapi_url=None)",
     "                  docs_url=None, redoc_url=None)",
     TH + "::test_the_api_schema_is_still_closed"),

    # ── §1/С11: порт 8011 в трёх местах ────────────────────────────────────
    # Расхождение не даёт ошибки ни в одном месте по отдельности: гардиан
    # стучится не туда, каждые 15 с поднимает ЖИВУЮ поверх живой, а проба
    # зелёная. Меньшее из двух чисел гасит большее МОЛЧА.
    ("порт пробы разъехался с портом панели", W,
     "PANEL_CLIENT_PORT = 8011",
     "PANEL_CLIENT_PORT = 8012",
     TN + "::test_the_watchdog_names_the_same_port"),

    ("порт гардиана разъехался с портом панели", G,
     "    [int]$Port = 8011,",
     "    [int]$Port = 8012,",
     TN + "::test_the_guardian_names_the_same_port"),

    ("гардиан поднимает панель на ЗАШИТОМ числе — четвёртое место для порта", G,
     "    $runnerArgs = @($runner, '--slug', $PanelSlug, '--port', [string]$PanelPort)",
     "    $runnerArgs = @($runner, '--slug', $PanelSlug, '--port', '8011')",
     TG + "::test_the_launch_never_hardcodes_the_port_it_starts_on"),

    # ═══════════════════════════════════════════════════════════════════════
    # ЗАХОД 2. Всё, что ниже, при первом прогоне гейта не имело сторожа
    # вовсе: сторожа звали ЧИСТЫЕ функции или читали ТЕКСТ скрипта, а между
    # «функция правильная» и «её правильно зовут» лежал зазор, в котором
    # живут самые дорогие дефекты арки. Зазор закрыт вызываемым шагом
    # (`Invoke-PanelGuardianCycle`), параметром `-MaxCycles` и ЖИВЫМ проходом
    # настоящим процессом (`tests/test_panel_client_guardian_live.py`).
    # ═══════════════════════════════════════════════════════════════════════

    # ── ДЫРА A: вердикт ЖИВОГО цикла ──────────────────────────────────────
    ("ЖИВОЙ вердикт: no_bind_address схлопнут в no_response — гардиан "
     "СНОСИТ здоровую панель при пропавшем тайнете", G,
     "    if (-not (Test-PanelAddressMeasurable -TailnetIp $TailnetIp -ExplicitHost $ExplicitHost -BindHost $BindHost)) {\n"
     "        return 'no_bind_address'\n"
     "    }",
     "    if (-not (Test-PanelAddressMeasurable -TailnetIp $TailnetIp -ExplicitHost $ExplicitHost -BindHost $BindHost)) {\n"
     "        return 'no_response'\n"
     "    }",
     TL + "::test_a_live_panel_survives_a_vanished_tailnet"),

    # ── ДЫРА B: ворота адреса ─────────────────────────────────────────────
    ("ворота адреса вывернуты: петля-фолбэк снова считается законным "
     "адресом (§2.3 отменён одной строкой)", G,
     "    if (-not $TailnetIp) { return $false }",
     "    if (-not $TailnetIp) { return $true }",
     TG + "::test_a_silent_fallback_is_not_measurable"),

    # ── ДЫРА C: ПРОВОДКА ЦИКЛА ────────────────────────────────────────────
    # Все четыре мутации ниже оставляют КАЖДУЮ чистую функцию идеально
    # правильной — и все четыре ломают гардиана.
    ("проводка: «не могу измерить» позвано как ОТКАЗ — пропавший тайнет сам "
     "доводит гардиана до исчерпанных отказов", G,
     "        $st = Step-RefusalState -Outcome 'unmeasurable' -Refusals $Refusals -Alerted $Alerted -MaxRefusals $MaxRefusals",
     "        $st = Step-RefusalState -Outcome 'refused' -Refusals $Refusals -Alerted $Alerted -MaxRefusals $MaxRefusals",
     TG + "::test_the_loop_does_not_count_an_unmeasurable_cycle_as_a_refusal"),

    ("проводка: $st.Alert не читается — вход в состояние не объявляется "
     "НИКОГДА, хотя чистая функция его честно возвращает", G,
     "        if ($st.Alert) {",
     "        if ($false) {",
     TG + "::test_the_loop_announces_the_exhausted_state_exactly_once"),

    ("проводка: строка о причине не пишется — §2.2 не выполнен при "
     "идеально правильной Format-RefusalLine", G,
     "        Write-G (Format-RefusalLine -Refusal (Get-RefusalReason) -Refusals $st.Refusals)",
     "        $null = Format-RefusalLine -Refusal (Get-RefusalReason) -Refusals $st.Refusals",
     TG + "::test_the_loop_writes_the_reason_on_every_single_attempt"),

    ("проводка: момент отказа не запоминается — пауза не наступает никогда, "
     "перезапуск раз в 15 с", G,
     "        $out.SinceRefusalSec = 0",
     "        # часы отказа не пошли",
     TG + "::test_the_loop_remembers_when_the_refusal_happened"),

    # 🔴 КЛАСС C ЦЕЛИКОМ. Шаг позван правильно и вернул правильное — а
    # результат выброшен. Ни один пофункциональный сторож этого не видит:
    # они проверяют ШАГ, а дефект живёт в том, кто его зовёт.
    ("ПРОВОДКА КЛАССА C: while зовёт шаг и ИГНОРИРУЕТ возвращённое "
     "состояние — счётчик, флаг и часы не переживают ни одного оборота", G,
     "        $state = Invoke-PanelGuardianCycle -Refusals $state.Refusals -Alerted $state.Alerted `",
     "        $null = Invoke-PanelGuardianCycle -Refusals $state.Refusals -Alerted $state.Alerted `",
     TL + "::test_the_refusal_clock_starts_on_a_real_pass"),

    # ── ДЫРА D: освобождение порта и разбор ответа ────────────────────────
    ("подъём больше не освобождает порт: зависший uvicorn держит 8011 ВЕЧНО, "
     "а Get-NetTCPConnection остаётся в файле и статику не краснит", G,
     "    if (-not (Stop-PanelPortOwner -PanelPort $PanelPort)) {",
     "    if ($false) {",
     TG + "::test_the_launch_frees_the_port_first_and_gives_up_if_it_cannot"),

    ("разбор неуспешного ответа выброшен: в PS 5.1 500 прилетает "
     "ИСКЛЮЧЕНИЕМ, и живая-но-больная панель станет неотличима от мёртвой", G,
     "        $resp = $null\n"
     "        try { $resp = $_.Exception.Response } catch { $resp = $null }\n"
     "        if ($resp) {\n"
     "            try { return [int]$resp.StatusCode } catch { return 0 }\n"
     "        }\n"
     "        return 0",
     "        return 0",
     TG + "::test_a_five_hundred_is_not_flattened_into_no_answer"),

    # ── регистратор: настройка выживания задачи ───────────────────────────
    ("задача регистрируется НЕ S4U — присмотр не переживёт разлогин, то есть "
     "панель умрёт ровно тем способом, ради которого арка и заведена", R,
     "    -LogonType S4U -RunLevel Highest",
     "    -LogonType Interactive -RunLevel Highest",
     TG + "::test_the_task_survives_logoff_and_reboot_like_its_three_neighbours"),

    # ПРИМАНКА. Прежний сторож искал подстроку «S4U» в СЫРОМ тексте и был бы
    # зелёным от одного комментария. Эта мутация не только снимает S4U с
    # вызова, но и дописывает слово обратно — в сообщение человеку, которое
    # `_ps_code` не снимает, потому что Write-Host это КОД. Красное здесь
    # доказывает, что сторож привязан к ВЫЗОВУ, а не к вхождению слова.
    ("приманка: S4U снят с вызова и дописан в Write-Host — сторож обязан "
     "смотреть на New-ScheduledTaskPrincipal, а не на слово в тексте", R,
     ["    -LogonType S4U -RunLevel Highest",
      "Write-Host \"[OK] Registered scheduled task '$TaskName' (S4U / Highest"],
     ["    -LogonType Interactive -RunLevel Highest",
      "Write-Host \"[OK] Registered scheduled task '$TaskName' (-LogonType S4U / Highest"],
     TG + "::test_the_task_survives_logoff_and_reboot_like_its_three_neighbours"),

    # ── ПРОВЕРКА ФАКТОМ: трюк с .Handle ───────────────────────────────────
    # Механизм назван владельцем лично. Без прикосновения к .Handle
    # PowerShell не кэширует системный хэндл, и после смерти процесса
    # `$p.ExitCode` возвращает $null: rc 1 становится НЕДОСТУПЕН, осознанный
    # отказ перестаёт отличаться от падения, счётчик отказов не растёт
    # НИКОГДА — и вся машинерия §2.2 (пауза 300, длинный интервал 1800,
    # однократный алерт) не включается вовсе. Остаётся бесконечный
    # перезапуск раз в 15 с, то есть ровно то, что §2.1 запрещает.
    #
    # Автор сторожей считает это непокрытым. Живой стенд, однако, ходит
    # ИМЕННО по ветке refused (подставная панель печатает причину и выходит с
    # rc 1), поэтому вопрос решается замером, а не рассуждением.
    ("трюк с .Handle убран: $p.ExitCode = $null, rc 1 недоступен, отказ "
     "неотличим от падения, §2.2 не включается вовсе", G,
     "    try { $null = $p.Handle } catch { Write-G \"не удалось закэшировать хэндл панели: $($_.Exception.GetType().Name)\" }",
     "    # хэндл не кэшируем",
     TL + "::test_the_refusal_clock_starts_on_a_real_pass"),

    # ── Get-PanelBind ИЗНУТРИ ─────────────────────────────────────────────
    # Числился непокрытым. Оказался покрыт: живой стенд подменяет ОКРУЖЕНИЕ
    # (свой `run_panel_client.py` во временном корне), а адрес гардиан
    # спрашивает именно у него — значит Get-PanelBind на живом проходе
    # исполняется по-настоящему, вместе со своим python-однострочником и
    # разбором его вывода. Обе мутации проверены фактом ДО внесения сюда.
    ("Get-PanelBind: тайнет всегда пуст (сентинел вместо ip) — вечный "
     "no_bind_address, гардиан не поднимет панель никогда", G,
     "        'print(ip if ip else 0)',",
     "        'print(0)',",
     TL + "::test_the_reason_from_the_panel_reaches_the_journal_on_a_real_pass"),

    ("Get-PanelBind: сентинел 0 не разворачивается в пустую строку — "
     "«тайнета нет» приезжает как адрес «0» и считается измеримым", G,
     "    $clean = { param($v) $s = (($v) + '').Trim(); if ($s -eq '0') { '' } else { $s } }",
     "    $clean = { param($v) (($v) + '').Trim() }",
     TL + "::test_a_live_panel_survives_a_vanished_tailnet"),
]


# ── чтение и запись: BOM обязан пережить мутацию ────────────────────────────
def read_source(rel: str) -> tuple[str, bool]:
    """(текст с LF, был ли BOM).

    BOM запоминается, потому что для `.ps1` он НЕСУЩИЙ: PowerShell 5.1 без
    него читает файл как cp1251, и дот-сорс падает на кириллице в комментарии
    — то есть гейт получил бы красное на КОДИРОВКЕ и записал его себе в
    заслугу как пойманную мутацию. Для `.py` BOM, наоборот, запрещён:
    `ast.parse` на нём краснеет, а сторожа этой арки читают исходники
    как текст.
    """
    raw = (ROOT / rel).read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    return text.replace("\r\n", "\n"), bom


def _newline(path) -> str:
    """Перевод строки, которым файл ЖИВЁТ на диске.

    Замерено, а не предположено: `.py` этого репозитория лежат в LF
    (`.gitattributes`: `*.py text eol=lf`), а `.ps1` — в CRLF. `write_text`
    с умолчанием перевёл бы ЛЮБОЙ файл в CRLF, и мутант отличался бы от
    оригинала не только мутацией, но и КАЖДОЙ строкой. Гейт обязан менять
    ровно то, что назвал: иначе «что именно проверяла эта мутация» перестаёт
    быть ответимым вопросом.

    Файла может не быть вовсе — мета-сторож DEV-26 пишет во ВРЕМЕННЫЙ каталог,
    а не в репозиторий. Тогда переводом считается LF: ничего не переводим и
    размер записи не трогаем (а размер здесь несущий — на нём стоит вся
    проверка байткода).
    """
    p = Path(path)
    if not p.is_file():
        return "\n"
    return "\r\n" if p.read_bytes().count(b"\r\n") else "\n"


def write_mutant(path, text: str, bom: bool = False) -> None:
    """Записать мутанта, вернуть BOM на место и выдать файлу СВОЙ mtime.

    ⚠️ ПОДПИСЬ — ОБЩИЙ КОНТРАКТ, А НЕ ЛИЧНОЕ ДЕЛО ЭТОГО ГЕЙТА.
    `tests/test_mutation_gate_bytecode.py` находит ВСЕ `scripts/mutate_*.py`,
    берёт у каждого `write_mutant` и проверяет ПОВЕДЕНИЕ: две записи одного
    размера обязаны получить разные mtime. Гейт, поменявший подпись, эту
    проверку не проваливает — он из-под неё ВЫХОДИТ: мета-сторож падает на
    `TypeError`, а гейт продолжает рапортовать «все мутации пойманы» с
    НИКЕМ НЕ ПРОВЕРЕННОЙ защитой от чужого байткода. Ровно тот класс, который
    эта арка и выкапывает, — вещь, выглядящая покрытием. Замерено 20.08:
    подпись была `(rel, text, bom)`, и мета-сторож не смог позвать её вовсе.

    Отсюда три обязательства, и все три — не стиль:
      * первый аргумент — НАСТОЯЩИЙ путь: мета-сторож пишет во временный
        каталог и в репозиторий не лезет;
      * `bom` — с умолчанием, чтобы вызов двумя аргументами работал;
      * подпись mtime ставится ВСЕГДА, в обеих ветках. Она защищает от
        байткода и к кодировке отношения не имеет.

    Относительные пути — забота `write_mutant_rel`, отдельным именем.

    Без подписи два мутанта одинакового размера в одну секунду делят один
    байткод, и второй прогон проверяет ПЕРВЫЙ код (DEV-26). Для `.ps1`
    подпись безвредна, для `.py` — обязательна.
    """
    p = Path(path)
    data = text.replace("\n", _newline(p)).encode("utf-8")
    p.write_bytes((codecs.BOM_UTF8 + data) if bom else data)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(p, (stamp, stamp))


def write_mutant_rel(rel: str, text: str, bom: bool) -> None:
    """То же, но путь ОТНОСИТЕЛЬНО корня репозитория — этим живёт весь гейт.

    Отдельным именем намеренно: удобство зовущего не имеет права менять
    подпись, по которой гейт проверяют снаружи.
    """
    write_mutant(ROOT / rel, text, bom)


# ── статическая сверка мишеней (способ №4) ──────────────────────────────────
_TEST_NAME = re.compile(r"^\s*def\s+(test_\w+)", re.M)


def _pairs(old, new) -> tuple[list, list]:
    """Правки мутации — всегда списками. Одна строка = список из одной.

    Правдоподобная правка иногда трогает два места сразу: приманка
    регистратора меняет значение в ВЫЗОВЕ и «чинит» слово в сообщении
    человеку. Разбивать её на две мутации значило бы проверять не то, что
    человек делает одним движением.
    """
    olds = list(old) if isinstance(old, (list, tuple)) else [old]
    news = list(new) if isinstance(new, (list, tuple)) else [new]
    return olds, news


def verify_targets() -> list[tuple[str, str]]:
    """Проверить КАЖДУЮ мишень ДО первой мутации. Файлы не меняются.

    Две половины мишени, и обе умеют уехать по отдельности:

    * ИСКОМАЯ СТРОКА в целевом файле обязана встречаться РОВНО ОДИН РАЗ.
      Ноль — `replace` молча ничего не делает, прогон идёт по НЕМУТИРОВАННОМУ
      коду, гейт печатает `[ok]` за мутацию, которой не было. Два — попадание
      в первое вхождение, и какое именно место мутировано, неизвестно;
    * ИМЯ ТЕСТА обязано существовать в названном файле. Переименованный
      сторож даёт rc 4 и «collected 0 items»: по критерию красноты он честно
      печатается слепым, но причина утонула бы в сотне строк прогона.
    """
    problems: list[tuple[str, str]] = []
    sources: dict[str, str] = {}
    tests: dict[str, set] = {}
    for name, rel, old, new, test in MUTATIONS:
        olds, news = _pairs(old, new)
        if len(olds) != len(news):
            problems.append((name, "правок %d, замен %d — список не парный"
                             % (len(olds), len(news))))
        if rel not in sources:
            if not (ROOT / rel).exists():
                problems.append((name, "нет целевого файла %s" % rel))
                sources[rel] = ""
            else:
                sources[rel] = read_source(rel)[0]
        for one in olds:
            found = sources[rel].count(one)
            if found != 1:
                problems.append(
                    (name, "фрагмент найден %d раз в %s (нужно РОВНО 1): %r"
                     % (found, rel, one.splitlines()[0][:90])))

        t_file, _sep, t_name = test.partition("::")
        if t_file not in tests:
            p = ROOT / t_file
            tests[t_file] = (set(_TEST_NAME.findall(p.read_text(encoding="utf-8")))
                             if p.exists() else set())
            if not p.exists():
                problems.append((name, "нет файла сторожей %s" % t_file))
        if t_name and tests[t_file] and t_name not in tests[t_file]:
            problems.append((name, "в %s нет теста %s — сторож переименован или "
                                   "удалён" % (t_file, t_name)))
    return problems


# ── прогон ──────────────────────────────────────────────────────────────────
def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — это РОВНО `rc 1` плюс `failed` в выводе (способ №2 шапки).
    Кодировка чтения задана ЯВНО (способ №3): ассерты этой арки написаны
    по-русски и по-украински, и одной заглавной «И» хватило бы, чтобы
    `text=True` уронил читающий поток на cp1251 и напечатал пойманную
    мутацию слепой.
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    """Откат идёт через `git checkout --`, то есть незакоммиченные правки он
    сотрёт. Один раз этого уже хватило, чтобы потерять готовую работу."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]

    problems = verify_targets()
    if problems:
        print("МИШЕНИ УЕХАЛИ ОТ КОДА — прогон НЕ начат "
              "(иначе гейт напечатал бы [ok] за неисполненную мутацию):")
        for name, why in problems:
            print("  [!] %s\n      %s" % (name, why))
        print("\nПлохих мишеней: %d из %d." % (len(problems), len(MUTATIONS)))
        return 2
    print("Статическая сверка: все %d мишеней на месте "
          "(каждая строка ровно один раз, каждый тест существует)."
          % len(MUTATIONS))
    if check_only:
        return 0

    refuse_if_live_tree(ROOT)
    assert_clean()

    blind = []
    for name, rel, old, new, test in MUTATIONS:
        text, bom = read_source(rel)
        olds, news = _pairs(old, new)
        if [o for o in olds if text.count(o) != 1]:
            # Сверка выше уже прошла; сюда попадём только если файл поменялся
            # ПОСРЕДИ прогона. Молчать нельзя — это ровно способ №4.
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент уехал по ходу прогона"
                  % name)
            blind.append((name, "фрагмент уехал по ходу прогона"))
            continue
        mutated = text
        for o, n in zip(olds, news):
            mutated = mutated.replace(o, n, 1)
        write_mutant_rel(rel, mutated, bom)
        try:
            caught, answer = run(test)
        finally:
            revert(rel)
        if caught:
            print("[ok]   %s -> сторож покраснел" % name)
        else:
            # Ответ pytest печатается ЦЕЛИКОМ: «остался зелёным» и «сбор
            # сломался» — разные беды, и чинят их по-разному.
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, answer))
            blind.append((name, answer))
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
