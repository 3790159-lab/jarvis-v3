"""Скриншоты панелей для ревью.

Страницы самодостаточны (CSS инлайном, ноль внешних ресурсов), поэтому HTML
забирается httpx'ом С ЗАГОЛОВКОМ авторизации и рендерится с диска. Так не
понадобилось делать обходной путь аутентификации ради картинок — ключ в URL
светился бы в истории и логах.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
KEY = "demo-local-key"
OUT = Path("docs/panels-screens")
CHROME = Path.home() / (
    "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe")

SHOTS = [
    ("01-tamapi-main", "/panel/tamapi", 1200, 1900, None),
    # Точка красится по СМЫСЛУ, а не по состоянию: «на зв'язку» — нейтральная
    # (зелёный отдан деньгам), «немає зв'язку» — красная. Поэтому подмен две.
    ("01b-tamapi-status-down", "/panel/tamapi", 1200, 420,
     [("<span class='dot calm'></span>", "<span class='dot broken'></span>"),
      ("<b>На зв&#x27;язку</b>", "<b>Немає зв&#x27;язку</b>")]),
    ("02-tamapi-main-mobile", "/panel/tamapi", 430, 1750, None),
    ("03-tamapi-paid-modal", "/panel/tamapi", 1200, 1000,
     ("class='modal' id='paidbox'", "class='modal show' id='paidbox'")),
    ("04-tamapi-pause-modal", "/panel/tamapi", 1200, 1000,
     ("class='modal' id='pausebox'", "class='modal show' id='pausebox'")),
    ("05-dynamics-month-2metrics",
     "/panel/tamapi/dynamics?m=dialogs&m=avg_check&period=month", 1200, 1250, None),
    ("06-dynamics-week-3metrics",
     "/panel/tamapi/dynamics?m=dialogs&m=qualified&m=handed&period=week", 1200, 1250, None),
    ("07-dynamics-day", "/panel/tamapi/dynamics?m=dialogs&period=day", 1200, 1250, None),
    ("08-jarvis-panel", "/panel/jarvis", 1200, 2300, None),
]


def main() -> int:
    if not CHROME.exists():
        print(f"chromium не найден: {CHROME}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path("state/_shots")
    tmp.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"X-Panels-Key": KEY}, timeout=60) as c:
        for name, url, w, h, patch in SHOTS:
            r = c.get(BASE + url)
            if r.status_code != 200:
                print(f"  [!] {name}: HTTP {r.status_code}")
                continue
            html = r.text
            for old, new in ([patch] if isinstance(patch, tuple) else (patch or [])):
                if old not in html:
                    print(f"  [!] {name}: паттерн подмены не найден: {old[:40]}")
                html = html.replace(old, new)
            f = tmp / f"{name}.html"
            f.write_text(html, encoding="utf-8")
            png = OUT / f"{name}.png"
            subprocess.run(
                [str(CHROME), "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=1", f"--window-size={w},{h}",
                 f"--screenshot={png.resolve()}", f.resolve().as_uri()],
                capture_output=True, timeout=120)
            ok = png.exists() and png.stat().st_size > 3000
            print(f"  {'OK ' if ok else '!! '} {name}.png"
                  f" ({png.stat().st_size // 1024 if png.exists() else 0} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
