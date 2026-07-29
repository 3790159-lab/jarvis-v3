"""Общая оболочка обеих панелей: стили, SVG-график, мелкие хелперы.

Ноль внешних ресурсов: CSS и JS инлайном, графики рисуются SVG руками. Причина
не в эстетике — панель обязана работать за auth и не тянуть ничего со сторонних
CDN (это и приватность, и работоспособность без интернета).

СЕКРЕТОВ ВО ФРОНТЕ НОЛЬ: сюда не приходят ни ключи, ни значения переменных
окружения, ни содержимое .env. Ключ панели живёт в HttpOnly-cookie и в разметку
не попадает.
"""
from __future__ import annotations

import html
import time

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#272c37;
  --tx:#e7eaf0; --dim:#98a2b3; --acc:#4f8cff; --ok:#2fbf71; --warn:#f0a92c;
  --bad:#e5484d; --off:#5a6273;
  --s1:#4f8cff; --s2:#f0a92c; --s3:#2fbf71;
}
body{background:var(--bg);color:var(--tx);
  font:15px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  padding:18px;max-width:1080px;margin:0 auto}
a{color:var(--acc);text-decoration:none}
h1{font-size:20px;margin-bottom:2px}
h2{font-size:15px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;
  margin:22px 0 10px;font-weight:600}
.sub{color:var(--dim);font-size:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin-bottom:12px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:8px;
  flex:0 0 auto}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
.off{background:var(--off)}
.btn{background:var(--panel2);border:1px solid var(--line);color:var(--tx);
  border-radius:9px;padding:8px 13px;font-size:13px;cursor:pointer;
  display:inline-block}
.btn:hover{border-color:var(--acc)}
.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.danger{border-color:var(--bad);color:#ff9b9e}
.btn.sm{padding:6px 10px;font-size:12px}
.pill{font-size:11px;color:var(--dim);border:1px solid var(--line);
  border-radius:20px;padding:2px 9px;display:inline-block}
.grid{display:grid;gap:10px}
.tiles{grid-template-columns:repeat(4,1fr)}
@media(max-width:760px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:11px 12px;cursor:pointer;user-select:none}
.tile.on{border-color:var(--sel,var(--acc));box-shadow:inset 0 0 0 1px var(--sel,var(--acc))}
.tile .k{font-size:12px;color:var(--dim)}
.tile .v{font-size:21px;font-weight:650;margin-top:3px}
.tile .d{font-size:12px;margin-top:1px}
.up{color:var(--ok)}.down{color:var(--bad)}.flat{color:var(--dim)}
.funnel{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap}
.fstep{flex:1;min-width:120px;background:var(--panel2);border:1px solid var(--line);
  border-radius:10px;padding:10px 12px}
.fstep .n{font-size:20px;font-weight:650}
.fstep .l{font-size:12px;color:var(--dim)}
.fstep .p{font-size:12px;color:var(--acc);margin-top:2px}
.bar{height:10px;background:var(--panel2);border-radius:6px;overflow:hidden;
  border:1px solid var(--line)}
.bar>i{display:block;height:100%;background:var(--ok)}
.bar.w>i{background:var(--warn)}.bar.b>i{background:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.empty{color:var(--dim);font-size:13px;padding:10px 2px}
.note{background:#20242e;border-left:3px solid var(--warn);padding:9px 12px;
  border-radius:0 8px 8px 0;font-size:13px;color:#e8d9b6;margin-bottom:12px}
.modal{position:fixed;inset:0;background:rgba(6,8,12,.72);display:none;
  align-items:center;justify-content:center;padding:20px;z-index:9}
.modal.show{display:flex}
.modal .box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:20px;max-width:420px}
.modal h3{font-size:16px;margin-bottom:8px}
.modal p{font-size:14px;color:var(--dim);margin-bottom:16px}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{background:transparent;border:0;color:var(--dim);padding:7px 14px;
  font-size:13px;cursor:pointer}
.seg button.on{background:var(--acc);color:#fff}
.legendline{display:inline-flex;align-items:center;gap:6px;margin-right:14px;
  font-size:12px;color:var(--dim)}
.sw{width:14px;height:3px;border-radius:2px;display:inline-block}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def ago(ts: float | None, now: float | None = None) -> str:
    """Возраст факта. Панель без возраста врёт при первом зависании сборщика."""
    if not ts:
        return "—"
    d = (now or time.time()) - ts
    if d < 60:
        return f"{int(d)} с тому"
    if d < 3600:
        return f"{int(d // 60)} хв тому"
    if d < 86400:
        return f"{int(d // 3600)} год тому"
    return f"{int(d // 86400)} дн тому"


def page(title: str, body: str, *, extra_js: str = "") -> str:
    return (
        "<!doctype html><html lang='uk'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        f"{body}<script>{extra_js}</script></body></html>"
    )


def delta_html(cur, prev) -> str:
    if cur is None or prev in (None, 0):
        return "<div class='d flat'>—</div>"
    d = (cur - prev) / prev * 100.0
    cls = "up" if d > 1 else ("down" if d < -1 else "flat")
    sign = "▲" if d > 1 else ("▼" if d < -1 else "•")
    return f"<div class='d {cls}'>{sign} {abs(d):.0f}%</div>"


def line_chart(series: list, *, width: int = 1000, height: int = 260) -> str:
    """SVG-график без библиотек.

    Деньги уходят на ПРАВУЮ ось и рисуются пунктиром (спека §6.2): 640 $ и
    3 оплаты на одной шкале превращают вторую линию в прямую по нулю. Пунктир —
    чтобы две оси нельзя было спутать формой.
    """
    if not series:
        return "<div class='empty'>Оберіть метрику вище.</div>"
    pad_l, pad_r, pad_t, pad_b = 46, 52, 14, 26
    W, H = width, height
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b

    left = [s for s in series if not s.money]
    right = [s for s in series if s.money]

    def bounds(group):
        vals = [v for s in group for _, v in s.points if v is not None]
        if not vals:
            return 0.0, 1.0
        lo, hi = min(vals + [0.0]), max(vals)
        return lo, (hi if hi > lo else lo + 1.0)

    lb = bounds(left)
    rb = bounds(right)
    n = max((len(s.points) for s in series), default=2)
    colors = ["var(--s1)", "var(--s2)", "var(--s3)"]
    out = [f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto'>"]

    for i in range(5):                                   # сетка + левая ось
        y = pad_t + ih * i / 4
        out.append(f"<line x1='{pad_l}' y1='{y:.1f}' x2='{W-pad_r}' y2='{y:.1f}' "
                   f"stroke='#272c37' stroke-width='1'/>")
        v = lb[1] - (lb[1] - lb[0]) * i / 4
        # Мелкий диапазон с шагом 1.25 давал подписи 0,1,2,4,5 — деления
        # выглядели неравномерными. Ниже 8 по шкале печатаем десятые.
        fmt = f"{v:.1f}" if (lb[1] - lb[0]) < 8 else f"{v:.0f}"
        out.append(f"<text x='{pad_l-8}' y='{y+4:.1f}' fill='#98a2b3' font-size='11' "
                   f"text-anchor='end'>{fmt}</text>")
    if right:
        for i in range(5):
            y = pad_t + ih * i / 4
            v = rb[1] - (rb[1] - rb[0]) * i / 4
            rfmt = f"{v:.1f}" if (rb[1] - rb[0]) < 8 else f"{v:.0f}"
            out.append(f"<text x='{W-pad_r+8}' y='{y+4:.1f}' fill='#f0a92c' "
                       f"font-size='11'>{rfmt}</text>")

    for idx, s in enumerate(series):
        lo, hi = rb if s.money else lb
        col = colors[idx % len(colors)]
        pts, gaps = [], 0
        for j, (_, v) in enumerate(s.points):
            if v is None:
                gaps += 1
                continue
            x = pad_l + (iw * j / max(n - 1, 1))
            y = pad_t + ih - ih * ((v - lo) / (hi - lo) if hi > lo else 0)
            pts.append((x, y))
        if not pts:
            continue
        dash = " stroke-dasharray='6 4'" if s.money else ""
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
        out.append(f"<path d='{d}' fill='none' stroke='{col}' stroke-width='2'{dash}/>")
        # Редкие данные честнее показывать точками, чем «трендом» (спека §6.3).
        if len(pts) <= 12:
            for x, y in pts:
                out.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' fill='{col}'/>")
    out.append("</svg>")

    legend = "".join(
        f"<span class='legendline'><i class='sw' style='background:{colors[i%3]}'></i>"
        f"{esc(s.label)}{' (права вісь, $)' if s.money else ''}</span>"
        for i, s in enumerate(series))
    return "".join(out) + f"<div style='margin-top:6px'>{legend}</div>"
