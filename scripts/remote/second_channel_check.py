# -*- coding: utf-8 -*-
"""Вердикт о ВТОРОМ КАНАЛЕ доступа (DEV-23). Stdlib-only, вызывается из
check_remote_status.ps1 — тот же приём, что chatter_watch_check.py.

Зачем отдельный вердикт. Старая проверка печатала `[ok] Tailscale service
Running` при канале, в который невозможно войти (`Logged out`), не давала
общего заключения и всегда возвращала 0. Это хуже, чем не проверять: правило
DEV-15 требует убедиться в живости второго канала ПЕРЕД операцией с
cloudflared, и такая проверка отвечала «есть» на «нет». Самоблокировка
2026-07-15 (SEV-1) повторилась бы ровно так же.

Здесь решается один вопрос: МОЖНО ЛИ ВОЙТИ ЧЕРЕЗ ЭТОТ КАНАЛ, если cloudflared
умрёт. Ответ зелёный только когда выполнено ВСЁ:
  * бэкенд tailscaled в состоянии Running (не NeedsLogin/Stopped);
  * у машины есть адрес в тайлнете;
  * в тайлнете есть хотя бы один ДРУГОЙ узел — конец, с которого входить.
Последнее неочевидно, но существенно: у канала два конца, и тайлнет из одной
машины каналом не является.

Запуск: python second_channel_check.py   (читает `tailscale status --json`)
Код возврата: 0 — годен, иначе не годен (можно использовать как гейт).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import namedtuple

Verdict = namedtuple("Verdict", "usable reason")

_RUNNING = "Running"


def second_channel_verdict(status_json: str) -> Verdict:
    """Пурная функция: текст `tailscale status --json` -> вердикт.

    Любая неожиданность (пустой ввод, не-JSON, отсутствующие ключи) — НЕ годен.
    Молчаливое «наверное, всё хорошо» здесь недопустимо: цена ошибки — потеря
    всех каналов доступа к проду."""
    try:
        data = json.loads(status_json or "")
    except (ValueError, TypeError):
        return Verdict(False, "не смог разобрать вывод `tailscale status --json`")
    if not isinstance(data, dict):
        return Verdict(False, "неожиданный формат `tailscale status --json`")

    backend = str(data.get("BackendState") or "")
    if backend != _RUNNING:
        if backend in ("NeedsLogin", "NoState", ""):
            return Verdict(False, "не выполнен вход — запусти `tailscale up` "
                                  "(служба может при этом крутиться, это не вход)")
        return Verdict(False, f"бэкенд tailscaled в состоянии {backend}, а не Running")

    self_node = data.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    if not ips:
        return Verdict(False, "вход выполнен, но у машины нет адреса (IP) в тайлнете")

    peers = data.get("Peer") or {}
    if not peers:
        return Verdict(False, "в тайлнете нет других устройств — входить неоткуда "
                              "(у канала два конца; добавь ноутбук/телефон)")

    names = sorted(str((p or {}).get("HostName") or "?") for p in peers.values())
    return Verdict(True, f"адрес {ips[0]}, узлов для входа: {len(peers)} ({', '.join(names[:3])})")


def exit_code(verdict: Verdict) -> int:
    return 0 if verdict.usable else 1


def summary_line(verdict: Verdict) -> str:
    """Итог одной строкой. Человек не должен собирать вывод глазами из
    пятнадцати разноцветных строчек — особенно перед сетевой операцией."""
    if verdict.usable:
        return f"ВТОРОЙ КАНАЛ ГОДЕН: {verdict.reason}"
    return (f"ВТОРОЙ КАНАЛ НЕ ГОДЕН: {verdict.reason}. "
            "НЕ трогай cloudflared/туннель/DNS — откатить будет нечем (DEV-15).")


def _tailscale_status_json() -> str:
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
        return out.stdout or ""
    except FileNotFoundError:
        return ""
    except Exception:
        # DEV-18: не молча. Пустая строка -> вердикт «не годен» с внятной причиной.
        print("[second_channel] сбой вызова tailscale", file=sys.stderr)
        return ""


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    raw = _tailscale_status_json()
    if not raw.strip():
        v = Verdict(False, "`tailscale status --json` ничего не вернул "
                           "(tailscale не установлен или служба не отвечает)")
    else:
        v = second_channel_verdict(raw)
    print(summary_line(v))
    return exit_code(v)


if __name__ == "__main__":
    sys.exit(main())
