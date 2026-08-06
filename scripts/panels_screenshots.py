"""Скриншоты панелей для ревью.

Страницы самодостаточны (CSS инлайном, ноль внешних ресурсов), поэтому HTML
забирается httpx'ом С ЗАГОЛОВКОМ авторизации и рендерится с диска. Так не
понадобилось делать обходной путь аутентификации ради картинок — ключ в URL
светился бы в истории и логах.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
KEY = "demo-local-key"
OUT = Path("docs/panels-screens")
CHROME = Path.home() / (
    "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe")

HEARTBEAT = Path(os.getenv("TAMAPI_HEARTBEAT", "state/chatter_heartbeat.txt"))

# name, url, width, height, patch, hb_age_sec
#   patch  — подмена разметки; допустима только для состояния ИНТЕРФЕЙСА
#            (открытая модалка), которое иначе не снять статикой.
#   hb_age — возраст heartbeat на время кадра. Красный статус снимается
#            НАСТОЯЩИМ протухшим heartbeat, а не подкраской заголовка: подмена
#            меняла только заголовок, и подпись под ним оставалась «відповідає».
#            Скриншот в доке обязан показывать то, что увидит клиент.
SHOTS = [
    ("01-tamapi-main", "/panel/tamapi", 1200, 1900, None, None),
    ("01b-tamapi-status-down", "/panel/tamapi", 1200, 420, None, 20 * 60),
    ("02-tamapi-main-mobile", "/panel/tamapi", 430, 1750, None, None),
    ("03-tamapi-paid-modal", "/panel/tamapi", 1200, 1000,
     ("class='modal' id='paidbox'", "class='modal show' id='paidbox'"), None),
    ("04-tamapi-pause-modal", "/panel/tamapi", 1200, 1000,
     ("class='modal' id='pausebox'", "class='modal show' id='pausebox'"), None),
    ("05-dynamics-month-2metrics",
     "/panel/tamapi/dynamics?m=dialogs&m=avg_check&period=month", 1200, 1250, None, None),
    ("06-dynamics-week-3metrics",
     "/panel/tamapi/dynamics?m=dialogs&m=qualified&m=handed&period=week", 1200, 1250, None, None),
    ("07-dynamics-day", "/panel/tamapi/dynamics?m=dialogs&period=day", 1200, 1250, None, None),
    ("08-jarvis-panel", "/panel/jarvis", 1200, 2300, None, None),
]


def main() -> int:
    if not CHROME.exists():
        print(f"chromium не найден: {CHROME}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path("state/_shots")
    tmp.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"X-Panels-Key": KEY}, timeout=60) as c:
        for name, url, w, h, patch, hb_age in SHOTS:
            if hb_age is not None:
                if not HEARTBEAT.exists():
                    print(f"  [!] {name}: heartbeat не найден — стенд поднят без --seed?")
                    continue
                os.utime(HEARTBEAT, (time.time() - hb_age,) * 2)
            try:
                r = c.get(BASE + url)
            finally:
                # Свежесть возвращаем СРАЗУ: иначе протухший heartbeat утечёт
                # в остальные восемь кадров и они молча станут красными.
                if hb_age is not None:
                    os.utime(HEARTBEAT, None)
            if r.status_code != 200:
                print(f"  [!] {name}: HTTP {r.status_code}")
                continue
            html = r.text
            if hb_age is not None and "Немає зв&#x27;язку" not in html:
                print(f"  [!] {name}: ожидался красный статус, а его нет")
            if patch:
                if patch[0] not in html:
                    print(f"  [!] {name}: паттерн модалки не найден")
                html = html.replace(*patch)
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
