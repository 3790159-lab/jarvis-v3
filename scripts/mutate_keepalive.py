"""DEV-26 для арки keep-alive: ломаем её сторожей по одному и требуем КРАСНОГО.

Предмет особенный, и поэтому гейт нужен острее обычного. Эта арка ЭКОНОМИТ
деньги, а не производит поведение: клиент, у которого keep-alive сломался,
выглядит совершенно здоровым — он отвечает лидам как прежде, просто дороже.
Сторож, который тут молчит, не даёт о себе знать НИКОГДА; узнают о нём по счёту
в конце месяца. Ровно так 23.07 умер кэш и сутки этого никто не видел.

ГЛАВНАЯ мутация стоит первой: она возвращает границу пород промаха с TTL на
период пинга. Это НЕ выдуманный дефект — ровно так спека и была написана до
20.08, и ветка алерта была мёртвым кодом под собственным планировщиком, потому
что пинг физически не приходит раньше, чем через период. Если сторожа не ловят
эту мутацию, они не ловят единственный дефект, который здесь уже случался.

Вторая по важности — пара на §6.1 (мутации 9 и 10). Отказ обязан быть точным в
ОБЕ стороны: снятый отказ пускает арку в конфигурацию, где она молча греет не
того клиента; расширенный отказ кричит ERROR на нормальном проде и через
неделю его перестают читать. Сторож, ловящий только одну сторону, разрешает
вторую.

Красное — РОВНО rc 1 плюс `failed`. rc 2 это «тест не собрался»; зачесть его
себе значит объявить сломанный прогон пойманной мутацией.

BOM здесь НЕ ставится: предмет мутации `.py`, а BOM в нём Python исполняет
молча, зато `ast.parse` краснеет — мутант «не применился» выглядел бы пойманным.

Прогон: .venv\\Scripts\\python.exe scripts/mutate_keepalive.py
(только в worktree — DEV-31, см. gate_guard).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime каждому мутанту: DEV-26 ловил случай, когда pytest исполнял
# ЧУЖОЙ байткод и гейт зеленел на неизменённом коде.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

K = "chatter/core/keepalive.py"
R = "chatter/telethon_run.py"
L = "chatter/config/loader.py"

REVERT_SCOPE = "chatter"

T = "tests/test_keepalive_guards.py"


def d(name: str) -> str:
    return f"{T}::{name}"


# ── Заготовки, слишком длинные для строчки таблицы ───────────────────────────

# AUDIT D4 наоборот: флаг дебаунса ставится на НЕДОСТАВЛЕННОМ алерте. Так
# выглядит «оптимизация», которую пишут, чтобы не долбить упавший notifier, —
# и она делает ровно то, от чего D4 защищает: сжигает окно на сутки, оставив
# владельца глухим, ни разу его не предупредив.
_BURN_DEBOUNCE = (
    """    if handle is None:
        log.error("keep-alive [%s/%s]: алерт НЕ ДОСТАВЛЕН (notifier вернул "
                  "None) — владелец о поломке НЕ ЗНАЕТ", cfg.slug, tag)
        return False""",
    """    if handle is None:
        log.error("keep-alive [%s/%s]: алерт НЕ ДОСТАВЛЕН (notifier вернул "
                  "None) — владелец о поломке НЕ ЗНАЕТ", cfg.slug, tag)
        await asyncio.to_thread(store.set_runtime_flag, key, str(now), ts=now)
        return False""",
)

# Порченый флаг гасит алерт МОЛЧА. Пишется как «ну наверное правка была»
# — и превращает сторожа в то, что он сторожит: битое значение в
# runtime_flags становится способом выключить алерт, не оставив следа.
# Порченый флаг гасит алерт МОЛЧА. После переезда разбора в `flag_ts` мутация
# бьёт в РЕШЕНИЕ вызывающего, а не в разбор: `flag_ts` честно вернул None
# («значение мусорное»), а `_config_changed_between` объявляет это законной
# сменой конфига. Так выглядит «ну наверное правка была» — и превращает сторожа
# в то, что он сторожит: битая строка в runtime_flags выключает алерт.
_CORRUPT_SILENCES = (
    """    ts = flag_ts(raw)
    if ts is None:""",
    """    ts = flag_ts(raw)
    if ts is None:
        return True
    if False:""",
)

# Тег пинга становится БОЕВЫМ. Самая дорогая из тихих мутаций: пинги
# перестают отличаться от вызовов лида, hit-rate и денежный замер §4
# испорчены, и испорчены НЕЗАМЕТНО — числа остаются правдоподобными.
_COMBAT_TAGS = (
    """TAG_MAP: dict[str, str] = {t: KEEPALIVE_TAG_PREFIX + t
                           for t in (BRAIN_TAG, CLASSIFIER_TAG)}""",
    """TAG_MAP: dict[str, str] = {t: t
                           for t in (BRAIN_TAG, CLASSIFIER_TAG)}""",
)

# §7 снят: греем префикс, которого никто ни разу не звал. Выглядит заботой
# («пусть будет тёплый»), а на деле создаёт запись по ставке 1h у клиента,
# который, возможно, вообще молчит — то есть тратит деньги ровно там, где
# порог объёма их бережёт.
_WARM_FROM_SCRATCH = (
    """        ts = last_call_ts.get(tag)
        if ts is None:
            continue""",
    """        ts = last_call_ts.get(tag)
        if ts is None:
            out.append(tag)
            continue""",
)

MUTATIONS = [
    # ── Главная: сегодняшний дефект, возвращённый на место ───────────────────
    ("ГЛАВНАЯ: граница пород промаха возвращена с TTL на период пинга — "
     "ровно тот дефект, из-за которого ветка алерта была мёртвым кодом", K,
     [("    if gap is None or gap >= CACHE_TTL_SEC:",
       "    if gap is None or gap >= PING_PERIOD_SEC:")],
     d("test_a1_broken_prefix_alert_reaches_the_owner")),

    # ── Тумблер, окно, порог: три поля клиента ───────────────────────────────
    ("тумблер клиента игнорируется — арка включена у всех, включая тех, кому "
     "она убыточна", K,
     [("    ka = cfg.settings.keepalive\n    if not ka.enabled:",
       "    ka = cfg.settings.keepalive\n    if False:")],
     d("test_k9_disabled_client_sends_zero_pings")),

    # Мутировать проверку окна в ОДНОМ месте бесполезно: их две (в `due_tags`
    # и в `run_keepalive_cycle`), и уцелевшая гасит мутанта. Это не слепота
    # сторожа, а эшелонированная защита в коде — поэтому бьём в САМ предикат:
    # единственную точку, где «окно» вообще определяется.
    ("окно суток игнорируется — пинги идут круглосуточно, счёт вдвое выше "
     "обещанного", K,
     [("    if window is None:", "    if True:")],
     d("test_window_outside_hours_suppresses_the_ping")),

    ("порог объёма игнорируется — тихий клиент платит за пинги, которые ему "
     "никогда не окупятся", K,
     [("    if dialogs < ka.min_dialogs_per_month:", "    if False:")],
     d("test_v1_volume_below_threshold_pings_nobody")),

    ("граница порога съехала на «строго больше» — клиент РОВНО на пороге "
     "выпадает, и порог молча стал другим числом", K,
     [("    if dialogs < ka.min_dialogs_per_month:",
       "    if dialogs <= ka.min_dialogs_per_month:")],
     d("test_v3_volume_exactly_at_threshold_pings")),

    # ── Доставка алерта ──────────────────────────────────────────────────────
    ("AUDIT D4 снят: недоставленный алерт СЖИГАЕТ дебаунс — владелец глух "
     "сутки, не получив ни одного сообщения", K, [_BURN_DEBOUNCE],
     d("test_a4_failed_delivery_does_not_burn_the_debounce")),

    ("дебаунс снят — сломанный префикс шлёт владельцу 29 карточек в сутки, "
     "и он перестаёт их читать", K,
     [("        elif now - last_ts < window:", "        elif False:")],
     d("test_a7_repeated_misses_do_not_storm_the_owner")),

    # ── Породы промаха ───────────────────────────────────────────────────────
    ("порченый флаг config_changed_ts гасит алерт МОЛЧА — битое значение "
     "становится способом выключить сторожа без следа", K, [_CORRUPT_SILENCES],
     d("test_c4_corrupt_flag_does_not_silence_the_alert_quietly")),

    ("смена конфига оправдывает промах ВСЕГДА, а не внутри разрыва — первая "
     "же правка базы знаний выключает алерт до конца жизни клиента", K,
     [("    return lo < ts <= hi", "    return True")],
     d("test_c3_config_change_before_the_gap_does_not_excuse_the_miss")),

    # ── §6.1: отказ обязан быть точным в ОБЕ стороны ─────────────────────────
    ("§6.1 снят: две персоны в процессе пингуют — боевой вызов соседа глушит "
     "прогрев, и запись остывает молча", R,
     [("    if len(personas) > 1 and any(b.cfg.settings.keepalive.enabled",
       "    if False and any(b.cfg.settings.keepalive.enabled")],
     d("test_b1_two_personas_ping_nobody")),

    ("§6.1 стал ложно-широким: ERROR на КАЖДОМ тике нормального прода, где "
     "тумблеры выключены — сигнал, красный при исправной работе", R,
     [("    if len(personas) > 1 and any(b.cfg.settings.keepalive.enabled",
       "    if len(personas) > 1 or any(b.cfg.settings.keepalive.enabled")],
     d("test_b5_two_personas_with_toggles_off_do_not_shout")),

    # ── Тег, свежесть, ключи конфига ─────────────────────────────────────────
    ("пинги пишутся под БОЕВЫМ тегом — hit-rate и денежный замер §4 "
     "испорчены незаметно, числа остаются правдоподобными", K, [_COMBAT_TAGS],
     d("test_k3_usage_of_a_ping_is_recorded_under_keepalive_tag_only")),

    ("свежесть не проверяется — пинг уходит поверх только что состоявшегося "
     "вызова лида, деньги тратятся на разогретое", K,
     [("        if now - ts > period_sec:", "        if True:")],
     d("test_k5_tag_called_recently_is_not_due")),

    ("§7 снят: греем префикс, который никто ни разу не звал — создание записи "
     "по ставке 1h там, где порог объёма как раз бережёт деньги", K,
     [_WARM_FROM_SCRATCH],
     d("test_k5_never_called_prefix_is_not_warmed_from_scratch")),

    ("`keepalive` выпал из _SETTINGS_KEYS — клиент с тумблером не поднимется "
     "вовсе, и узнаем мы это на боевом рестарте", L,
     [('    "payments", "keepalive",', '    "payments",')],
     d("test_k10_keepalive_is_a_known_top_level_key")),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: rc 2 это «тест не собрался», и зачесть его себе значит объявить
    сломанный прогон пойманной мутацией."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert() -> None:
    subprocess.run(["git", "checkout", "--", REVERT_SCOPE], cwd=ROOT, check=True)


def dirty(scope=None) -> str:
    cmd = ["git", "status", "--porcelain", "--untracked-files=no"]
    if scope:
        cmd += ["--", scope]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def assert_clean() -> None:
    """Дерево обязано быть чистым ТАМ, КУДА БЬЁТ ОТКАТ: `git checkout --`
    сотрёт незакоммиченное."""
    inside = dirty(REVERT_SCOPE)
    if inside:
        raise SystemExit(
            "ОТКАЗ: боевой код грязный — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + inside)
    outside = dirty()
    if outside:
        print("[i] вне зоны отката есть незакоммиченные правки — сторожа "
              "проверяются В ЭТОМ виде, а не в виде коммита:")
        for line in outside.splitlines():
            print("      " + line)
        print()


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == text:
            print(f"[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: {name}")
            blind.append((name, "текст не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert()
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name}")

    print(f"\nмутаций {len(MUTATIONS)}, поймано {len(MUTATIONS) - len(blind)}")
    if blind:
        print("СЛЕПЫЕ:")
        for name, where in blind:
            print(f"  - {name} ({where})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
