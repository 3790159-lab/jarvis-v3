"""Приёмка панелей на узком экране: страница НЕ имеет права ехать вбок.

Зачем скриптом, а не глазами по скриншоту: обрезанный правый край видно только
если знать, что там что-то было. 12.08 на 390 px за краем молча жили колонка
«Останнє» в диалогах, половина кнопки «Зупинити всіх», «добовий ліміт» и три из
четырёх колонок таблицы арок — скриншот при этом выглядел опрятно.

Меряем В БРАУЗЕРЕ: только он знает реальные ширины после переносов, шрифтов и
медиазапросов. Виноватым считается САМЫЙ ГЛУБОКИЙ элемент за краем — родитель
шире всегда, и списком родителей отчёт превращается в шум.

Запуск (сервер поднимать отдельно):
    python scripts/panels_demo.py --seed --port 8099
    python scripts/panels_mobile_check.py [--shots КАТАЛОГ] [--width 390]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8099"
KEY = "demo-local-key"

PAGES = [
    ("tamapi-main", "TAMAPI · головний", "/panel/tamapi"),
    ("tamapi-dynamics", "TAMAPI · динаміка",
     "/panel/tamapi/dynamics?m=dialogs&m=avg_check&period=month"),
    ("jarvis", "Панель Джарвіса", "/panel/jarvis"),
]

# Порог в 1 px — против дробных ширин после масштабирования шрифтов, а не
# запас «на глаз»: реальное переполнение здесь начинается с десятков пикселей.
SLACK = 1

PROBE = """() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (r.right <= vw + 1 && r.left >= -1) continue;
    let deepest = true;
    for (const ch of el.children) {
      const cr = ch.getBoundingClientRect();
      if (cr.right > vw + 1 || cr.left < -1) { deepest = false; break; }
    }
    if (!deepest) continue;
    out.push({tag: el.tagName.toLowerCase(),
              cls: el.getAttribute('class') || '',
              right: Math.round(r.right),
              text: (el.textContent || '').trim().slice(0, 60)});
  }
  return {vw, scroll: document.documentElement.scrollWidth,
          offenders: out.slice(0, 12)};
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--shots", default=None,
                    help="каталог для скриншотов приёмки")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    bad = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": a.width, "height": 844},
                             device_scale_factor=2,
                             extra_http_headers={"X-Panels-Key": KEY})
        pg = ctx.new_page()
        for slug, title, url in PAGES:
            resp = pg.goto(BASE + url, wait_until="load")
            if not resp or resp.status != 200:
                print(f"[!] {title}: HTTP {resp.status if resp else '—'}")
                bad += 1
                continue
            if shots:
                pg.screenshot(path=str(shots / f"{slug}.png"), full_page=True)
            res = pg.evaluate(PROBE)
            over = res["scroll"] - res["vw"]
            if over <= SLACK and not res["offenders"]:
                print(f"[ok] {title}: окно {res['vw']} px, за краем ничего")
                continue
            bad += 1
            print(f"[!!] {title}: страница {res['scroll']} px при окне "
                  f"{res['vw']} px (+{over})")
            for o in res["offenders"]:
                cls = f".{o['cls'].replace(' ', '.')}" if o["cls"] else ""
                print(f"     {o['tag']}{cls} → правый край {o['right']} px"
                      f"   «{o['text']}»")
        ctx.close()
        br.close()

    print("ИТОГ:", "переполнения нет" if not bad else f"экранов с бедой: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
