"""Сборщик состояния фермы для панели Джарвиса (docs/jarvis-panel/PHASE0_READONLY.md).

СТРОГО read-only: ничего не запускает, не убивает и не правит. Единственная
запись — собственный снапшот (кэш в памяти процесса).

Три грабли зашиты здесь намеренно, потому что все три уже стоили ложных выводов:
  1. фильтр процессов по CommandLine МАТЧИТ САМ СЕБЯ (строка попадает в
     командную строку сборщика) → «раннер жив», когда его нет;
  2. LastTaskResult у Running-таска ничего не значит;
  3. на NTFS размер живого лога врёт (0 байт при открытом write-хэндле) —
     возраст берём временем модификации/чтением, не размером.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.getenv("JARVIS_ROOT", "C:/jarvis"))
HEARTBEAT_FRESH = 90.0

# Процессы фермы: (ключ, подпись, маркер в командной строке, только python?)
PROC_SPECS = [
    ("backend",  "backend :8010",  "run_backend_detached", True),
    ("bot",      "головний бот",   "jarvis_smart_telegram_control", True),
    ("chatter",  "chatter раннер", "chatter.telethon_run", True),
]

GUARDIAN_SPECS = [
    ("backend_guardian", "backend_guardian_detached.ps1", "guardian_heartbeat.txt"),
    ("bot_guardian",     "bot_guardian_detached.ps1",     "bot_heartbeat.txt"),
    ("chatter_guardian", "chatter_guardian_detached.ps1", "chatter_guardian_heartbeat.txt"),
    ("ops_watchdog",     "ops_watchdog_detached.ps1",     "ops_watchdog_heartbeat.txt"),
]

# Отставлено НАМЕРЕННО — отдельный класс, не авария. Панель, красящая это
# красным, ежедневно требует чинить нечинимое, и её перестают читать.
RETIRED = {"JarvisSniperDetached": "RunPod-півот: тригерів немає, сам не підніметься"}


@dataclass
class Row:
    key: str
    label: str
    state: str            # ok | warn | bad | off
    detail: str = ""
    extra: dict = field(default_factory=dict)


def _now() -> float:
    return time.time()


def _own_pids() -> set[int]:
    """Свой PID и предки — чтобы фильтр процессов не поймал сам себя."""
    out = {os.getpid()}
    try:
        import psutil
        p = psutil.Process(os.getpid())
        for anc in p.parents():
            out.add(anc.pid)
    except Exception:
        pass
    return out


def processes() -> list[Row]:
    try:
        import psutil
    except Exception:
        return [Row("procs", "процеси", "warn", "psutil недоступний")]

    mine = _own_pids()
    snap = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "ppid"]):
        try:
            snap.append((p.info["pid"], (p.info["name"] or "").lower(),
                         " ".join(p.info["cmdline"] or []), p.info["create_time"],
                         p.info["ppid"]))
        except Exception:
            continue

    rows = []
    for key, label, marker, py_only in PROC_SPECS:
        hits = [
            s for s in snap
            if marker in s[2]
            and s[0] not in mine                     # ГРАБЛЯ 1: не считать себя
            and (not py_only or s[1].startswith("python"))
        ]
        if hits:
            pids = "/".join(str(h[0]) for h in hits[:3])
            started = min(h[3] for h in hits)
            rows.append(Row(key, label, "ok", f"PID {pids}",
                            {"since": started, "count": len(hits)}))
        else:
            rows.append(Row(key, label, "bad", "процес не знайдено"))
    return rows


def guardians() -> list[Row]:
    rows = []
    mine = _own_pids()
    try:
        import psutil
        snap = [(p.info["pid"], " ".join(p.info["cmdline"] or []))
                for p in psutil.process_iter(["pid", "cmdline"])]
    except Exception:
        snap = []
    for key, script, hb in GUARDIAN_SPECS:
        alive = [s for s in snap if script in s[1] and s[0] not in mine]
        age = heartbeat_age(hb)
        fresh = age is not None and age < HEARTBEAT_FRESH
        if alive and fresh:
            state, detail = "ok", f"PID {alive[0][0]} · heartbeat {int(age)} с тому"
        elif alive or fresh:
            # ЖЁЛТЫЙ — не косметика: расхождение «процесс жив, heartbeat мёртв»
            # это ровно тот случай, когда сторож и гардиан считали DOWN по-разному.
            state = "warn"
            detail = ("процес є, heartbeat протух" if alive
                      else "heartbeat свіжий, процесу не видно")
        else:
            state, detail = "bad", "не працює"
        rows.append(Row(key, key, state, detail, {"hb_age": age}))
    return rows


def heartbeat_age(name: str) -> float | None:
    """Возраст heartbeat-файла. ГРАБЛЯ 3: размер файла на NTFS врёт, поэтому
    смотрим mtime, а не длину."""
    p = ROOT / "state" / name
    try:
        return _now() - p.stat().st_mtime
    except OSError:
        return None


def scheduled_tasks() -> list[Row]:
    """Плановые задачи. ГРАБЛЯ 2: у Running-таска LastTaskResult не значит
    ничего (у живых гардианов там 2147946720) — авторитетен State."""
    ps = (
        "Get-ScheduledTask | Where-Object { $_.TaskName -match 'Jarvis' } | "
        "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
        "\"$($_.TaskName)|$($_.State)|$($i.LastTaskResult)|$($i.LastRunTime)\" }"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                              "-Command", ps], capture_output=True, timeout=30,
                             text=True, encoding="utf-8", errors="replace").stdout
    except Exception as e:
        return [Row("tasks", "планові задачі", "warn", f"не зчитано: {type(e).__name__}")]

    rows = []
    for line in (out or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        name, state, last, when = parts[0], parts[1], parts[2], parts[3]
        if name in RETIRED:
            rows.append(Row(name, name, "off", RETIRED[name], {"last_run": when}))
            continue
        if state == "Running":
            rows.append(Row(name, name, "ok", "Running", {"last_run": when}))
        elif state == "Ready":
            ok = last.strip() in ("0", "267009")
            rows.append(Row(name, name, "ok" if ok else "warn",
                            f"Ready · останній результат {last}", {"last_run": when}))
        else:
            rows.append(Row(name, name, "warn", state, {"last_run": when}))
    return rows


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.run(["git"] + args, cwd=str(cwd or ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def arcs() -> list[dict]:
    """Ветки/worktree с возрастом. Возраст — та колонка, которой сейчас нет
    нигде, и именно она превращает список в решение («мёртвое — снести»)."""
    out = []
    raw = _git(["worktree", "list", "--porcelain"])
    cur: dict = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur:
                out.append(cur)
            cur = {"path": line.split(" ", 1)[1]}
        elif line.startswith("branch "):
            # Только refs/heads/, а не последний сегмент: split("/")[-1] резал
            # «arc/p20-suppression» до «p20-suppression» — и имя врало, и сверка
            # со списком смерженных промахивалась.
            cur["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "", 1)
        elif line.startswith("detached"):
            cur["branch"] = "(detached)"
    if cur:
        out.append(cur)

    # Список смерженных считаем ОДИН раз, а не в цикле по 21 worktree.
    merged_names = {b.strip().lstrip("* ").strip()
                    for b in _git(["branch", "--merged", "phase-4.0-unified-jarvis"]).splitlines()}
    for w in out:
        p = Path(w["path"])
        w["dirty"] = bool(_git(["status", "--porcelain"], p))
        last = _git(["log", "-1", "--format=%ct|%s"], p)
        if "|" in last:
            ts, subj = last.split("|", 1)
            try:
                w["age_days"] = round((_now() - float(ts)) / 86400.0, 1)
            except ValueError:
                w["age_days"] = None
            w["subject"] = subj[:70]
        w["merged"] = w.get("branch", "") in merged_names
    return out


def events(limit: int = 40) -> list[dict]:
    """Лента: control_events клиента + строки гардианов. «Посчитано ≠ доехало» —
    доставку алертов мы сегодня не журналируем, и это помечено как пробел."""
    out = []
    db = os.getenv("TAMAPI_DB", str(ROOT / ".secrets/demo.db"))
    try:
        from app.services.tamapi_metrics import _ro
        with _ro(db) as c:
            for r in c.execute(
                    "SELECT kind, contact_id, detail, ts FROM control_events "
                    "ORDER BY id DESC LIMIT ?", (limit,)):
                out.append({"src": "chatter", "kind": r["kind"],
                            "detail": r["detail"] or r["contact_id"] or "",
                            "ts": r["ts"]})
    except Exception:
        pass

    log = ROOT / "logs" / "chatter_guardian.stdout.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        for ln in lines:
            if "DOWN" in ln or "launched" in ln or "failed" in ln:
                out.append({"src": "guardian", "kind": "runner", "detail": ln[:120],
                            "ts": None})
    except OSError:
        pass
    return out[:limit]


def api_keys() -> list[dict]:
    """ТОЛЬКО метаданные: имя, назначение, срок, здоровье. Значения не читаются,
    не расшифровываются и не маскируются — их здесь просто нет."""
    known = [
        {"name": "Instagram (Path B)", "purpose": "публикация/лента IG",
         "expires": "2026-09-09", "auto": "JarvisIgTokenRefresh",
         "note": "хвіст: скинути IG App Secret (світився)"},
        {"name": "ANTHROPIC_API_KEY", "purpose": "brain + класифікатор",
         "expires": None, "auto": None, "note": "основна витрата"},
        {"name": "FAL_KEY", "purpose": "FLUX.2 генерація",
         "expires": None, "auto": None, "note": "⚠️ потребує ротації"},
        {"name": "WaveSpeed", "purpose": "анімація",
         "expires": None, "auto": None, "note": "⚠️ витікав — ротувати"},
        {"name": "OpenAI", "purpose": "voice", "expires": None, "auto": None, "note": ""},
        {"name": "Telegram (бот + пульт)", "purpose": "канали керування",
         "expires": None, "auto": None, "note": "різні токени"},
    ]
    today = time.gmtime()
    for k in known:
        if k["expires"]:
            try:
                exp = time.strptime(k["expires"], "%Y-%m-%d")
                days = int((time.mktime(exp) - time.mktime(today)) / 86400)
                k["days_left"] = days
                k["state"] = "bad" if days < 7 else ("warn" if days < 30 else "ok")
            except ValueError:
                k["days_left"], k["state"] = None, "warn"
        else:
            k["days_left"] = None
            k["state"] = "warn" if "⚠️" in (k["note"] or "") else "ok"
    return known


def external_watchdog() -> Row:
    """§0 спеки: панель обязана говорить о собственной слепоте. Пока внешний
    сторож не настроен — красная строка на самом видном месте."""
    url = (os.getenv("HEALTHCHECKS_URL") or "").strip()
    if not url:
        return Row("ext", "Зовнішній сторож", "bad",
                   "НЕ налаштований — панель не бачить смерті самої машини")
    state_file = ROOT / "state" / "healthchecks_last.txt"
    age = None
    try:
        age = _now() - state_file.stat().st_mtime
    except OSError:
        pass
    if age is None:
        return Row("ext", "Зовнішній сторож", "warn",
                   "URL задано, але пінгів ще не було")
    if age > 300:
        return Row("ext", "Зовнішній сторож", "warn",
                   f"останній пінг {int(age // 60)} хв тому")
    return Row("ext", "Зовнішній сторож", "ok", f"пінг {int(age)} с тому")


def snapshot() -> dict:
    return {
        "collected_at": _now(),
        "external": external_watchdog(),
        "processes": processes(),
        "guardians": guardians(),
        "tasks": scheduled_tasks(),
        "arcs": arcs(),
        "events": events(),
        "keys": api_keys(),
    }
