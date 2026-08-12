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
  --tx:#e7eaf0; --dim:#98a2b3; --acc:#4f8cff;
  /* Три смысла — три цвета, и ни один из них не занят ничем ещё: зелёный —
     деньги, янтарь — «чекає вас», красный — «зламано». Спокойное состояние
     цвета НЕ получает вовсе: когда на экране выделено двадцать строк из
     двадцати, не выделено ничего. Раньше красным были разом авария связи,
     штатная кнопка паузы и просроченная карточка — и красный перестал
     означать «беда». */
  --ok:#2fbf71; --warn:#f0a92c; --bad:#e5484d;
  --calm:#7b8698; --off:#5a6273;
  --s1:#4f8cff; --s2:#f0a92c; --s3:#2fbf71;
}
body{background:var(--bg);color:var(--tx);
  font:15px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  padding:18px;max-width:1080px;margin:0 auto}
a{color:var(--acc);text-decoration:none}
/* ШКАЛА КЕГЛЕЙ — РОВНО ЧЕТЫРЕ: 32 ответ, 24 число, 15 текст, 12 подпись.
   Было шесть в диапазоне 11-21: 13 и 15 рядом не читаются как два уровня,
   они читаются как небрежность. Пишем числами, а не переменными, — сторож
   шкалы обязан видеть кегль в тексте стиля. */
h1{font-size:32px;line-height:1.15;font-weight:700;margin-bottom:4px}
h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;
  margin:20px 0 8px;font-weight:600}
/* Ответ. Стоит первым и крупнее всего на экране: сверху обязан быть ВЫВОД,
   а не заголовок страницы и не первая попавшаяся цифра. */
.ans{color:var(--tx)}
.ans.wait{color:var(--warn)}
.ans.broken{color:var(--bad)}
.sub{color:var(--dim);font-size:12px}
/* Подпись НА МЕСТЕ значения: пустое место в цифровом слоте читается как ноль,
   а «—» — как «значение есть, но какое-то не такое». */
.na{color:var(--dim);font-size:12px}
.money{color:var(--ok)}
.wait{color:var(--warn)}
.broken{color:var(--bad)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin-bottom:12px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;
  flex-wrap:wrap}
/* Статус — одна строка, а не карточка в два этажа на самом видном месте.
   Он и стоит теперь ПОСЛЕ долга и денег: «бот жив» — это не новость, новостью
   он становится ровно тогда, когда бот НЕ жив, а это видно по ответу сверху. */
.statusline{padding:10px 14px}
.ell{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Флекс- и грид-элементы по умолчанию получают `min-width:auto` и отказываются
   сжиматься уже своего содержимого — они выталкивают ВСЮ страницу в
   горизонтальный скролл. На 390 px это резало не оформление, а факты и
   действия: колонку «Останнє» целиком, половину кнопки «Зупинити всіх»,
   «добовий ліміт», три из четырёх колонок таблицы арок. Ограничитель ставим
   здесь, у источника; `overflow-x:hidden` на body был бы не починкой, а
   заклеенным индикатором — контент так же остался бы обрезанным. */
.row>*,.grid>*,.funnel>*,.tile,.fstep{min-width:0}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:8px;
  flex:0 0 auto}
/* Норма — нейтральная точка, а не зелёная: зелёный отдан деньгам. Выключенное
   — кольцо, чтобы «норма» и «вимкнено» различались не только словом рядом. */
.dot.calm{background:var(--calm)}
.dot.wait{background:var(--warn)}
.dot.broken{background:var(--bad)}
.dot.off{background:transparent;box-shadow:inset 0 0 0 2px var(--off)}
.btn{background:var(--panel2);border:1px solid var(--line);color:var(--tx);
  border-radius:9px;padding:9px 14px;font-size:15px;cursor:pointer;
  display:inline-block}
.btn:hover{border-color:var(--acc)}
.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
/* Красная кнопка есть только у подтверждения. Кнопка на экране ничего не
   ломает — она задаёт вопрос. */
.btn.broken{border-color:var(--bad);color:#ff9b9e}
.btn.sm{padding:6px 10px;font-size:12px}
.pill{font-size:12px;color:var(--dim);border:1px solid var(--line);
  border-radius:20px;padding:2px 9px;display:inline-block}
.grid{display:grid;gap:10px}
.tiles{grid-template-columns:repeat(4,1fr)}
@media(max-width:760px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:11px 12px;cursor:pointer;user-select:none;color:var(--tx);
  display:block}
.tile.on{border-color:var(--sel,var(--acc));box-shadow:inset 0 0 0 1px var(--sel,var(--acc))}
/* Плитка без истории гаснет и перестаёт быть ссылкой: тап по ней
   перерисовывал график в пустоту, а выглядела она рабочей. */
.tile.off{opacity:.5;cursor:default}
.tile .k{font-size:12px;color:var(--dim)}
.tile .v{font-size:24px;font-weight:650;margin-top:3px;overflow-wrap:break-word}
.tile .d{font-size:12px;margin-top:1px}
/* Дельта направление показывает стрелкой, а не цветом: рост «Тривалості
   ведення» — не хорошая новость, и красить его зелёным значит подсказывать
   неверный вывод. */
.up,.down,.flat{color:var(--dim)}
.funnel{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap}
.fstep{flex:1;min-width:120px;background:var(--panel2);border:1px solid var(--line);
  border-radius:10px;padding:10px 12px}
.fstep .n{font-size:24px;font-weight:650}
.fstep .l{font-size:12px;color:var(--dim)}
.fstep .p{font-size:12px;color:var(--acc);margin-top:2px}
/* Карточка «Требує вас»: две строки. Первая — кто и СКОЛЬКО ждёт, вторая —
   последняя реплика и одно действие. Остальные действия уезжают под «⋯».
   Прячем ДЕЙСТВИЯ, не ДОЛГИ: оба возраста и пометка мёртвого лида остаются
   в первой строке при любой перекладке. */
.att{background:var(--panel2);padding:11px 14px}
.more{position:relative;display:inline-block}
.more>summary{list-style:none;display:inline-block}
.more>summary::-webkit-details-marker{display:none}
.more .menu{position:absolute;right:0;top:calc(100% + 6px);z-index:5;
  display:flex;flex-direction:column;gap:6px;min-width:180px;padding:8px;
  background:var(--panel);border:1px solid var(--line);border-radius:11px;
  box-shadow:0 10px 26px rgba(0,0,0,.45)}
.more .menu .btn{text-align:left}
/* Полоска пакета: основная часть + СЕГМЕНТ ПЕРЕРАСХОДА. Оба видимы разом,
   поэтому flex, а не абсолютное позиционирование: перерасход не имеет права
   быть невидимым, как было при `min(pct,100)` на одной шкале. */
.bar{height:10px;background:var(--panel2);border-radius:6px;overflow:hidden;
  border:1px solid var(--line);display:flex}
.bar>i{display:block;height:100%;background:var(--calm)}
/* Пакет — это деньги, поэтому зелёный. Исчерпанный пакет НЕ авария: бот
   продолжает работать, решение выключить принимает владелец — значит янтарь
   «чекає вас», а не красный «зламано». */
.bar.money>i{background:var(--ok)}
.bar.wait>i{background:var(--warn)}
.bar>b.over{display:block;height:100%}
.bar.wait>b.over{background:repeating-linear-gradient(
  45deg,var(--warn),var(--warn) 3px,transparent 3px,transparent 6px)}
table{width:100%;border-collapse:collapse;font-size:15px}
td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:none}
/* Пути worktree'ов и имена ключей — сплошные строки без пробелов: перенести
   их не по чему, и на узком экране они распирают страницу в одиночку. */
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;
  overflow-wrap:anywhere}
.empty{color:var(--dim);font-size:15px;padding:10px 2px}
.note{background:#20242e;border-left:3px solid var(--line);padding:9px 12px;
  border-radius:0 8px 8px 0;font-size:15px;color:#e8d9b6;margin-bottom:12px}
.note.wait{border-left-color:var(--warn)}
.note.broken{border-left-color:var(--bad)}
.modal{position:fixed;inset:0;background:rgba(6,8,12,.72);display:none;
  align-items:center;justify-content:center;padding:20px;z-index:9}
.modal.show{display:flex}
.modal .box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:20px;max-width:420px}
.modal h3{font-size:24px;margin-bottom:8px}
.modal p{font-size:15px;color:var(--dim);margin-bottom:16px}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{background:transparent;border:0;color:var(--dim);padding:8px 14px;
  font-size:15px;cursor:pointer}
.seg button.on{background:var(--acc);color:#fff}
.legendline{display:inline-flex;align-items:center;gap:6px;margin-right:14px;
  font-size:12px;color:var(--dim)}
.sw{width:14px;height:3px;border-radius:2px;display:inline-block}
/* Подпись состояния РЯДОМ с точкой, а не вместо неё. Цвет у подписи
   намеренно нейтральный: оттенок уже несёт точка, а красный как цвет текста
   даёт 4.45 к фону — впритык мимо AA. */
.slab{font-size:12px;color:var(--dim);text-transform:uppercase;
  letter-spacing:.04em;margin-right:7px}

/* ─────────────────────────── Узкий экран (телефон) ───────────────────────
   Основной сценарий панели — телефон, а не монитор: владелец открывает её на
   ходу. Поэтому мобильная раскладка здесь не «деградация» широкой, а первый
   класс.

   Таблица на 3-5 колонок физически не умещается в 390 px: браузер не режет
   её, он растягивает страницу, и вместе с таблицей за край уезжает ВЕСЬ
   остальной экран. Единственная честная раскладка — строка становится
   карточкой, а заголовок колонки переезжает в подпись перед значением
   (`data-l`). Ячейка без `data-l` — главная в строке (имя лида, ветка), ей
   подпись не нужна: она и так первая. */
@media(max-width:620px){
  body{padding:14px 12px}
  .fstep{min-width:calc(50% - 3px)}
  table{display:block}
  thead{display:none}
  table tr{display:block;padding:9px 0;border-bottom:1px solid var(--line)}
  table tr:last-child{border-bottom:none}
  /* Главная ячейка остаётся блоком: её содержимое (имя ветки + путь, имя лида
     + последняя реплика) сверстано «одно под другим», и flex развернул бы эту
     пару в строку. Ряд «подпись — значение» нужен только помеченным ячейкам. */
  /* `anywhere`, а не `break-word`: только он участвует в расчёте min-content, а
     ширину карточке диктует именно она. Лента событий несёт машинные строки без
     единого пробела («deadline:30,price:40,…», 56 символов) — фразе есть где
     перенестись, такой строке негде, и одна ячейка распирала таблицу до 433 px
     при окне 390. */
  table td{display:block;border:none;padding:2px 0;overflow-wrap:anywhere}
  table td[data-l]{display:flex;gap:10px;align-items:baseline}
  table td>*{min-width:0}
  table td[data-l]::before{content:attr(data-l);flex:0 0 84px;
    color:var(--dim);font-size:12px;text-transform:uppercase;
    letter-spacing:.04em;line-height:1.5}
  /* Пустая ячейка в карточке — просто пустая строка с подписью ни о чём. */
  table td[data-l]:empty{display:none}
}
"""

# Слово к каждому состоянию. Без него строки «Ready · останній результат 0» и
# «… 1» различались ИСКЛЮЧИТЕЛЬНО оттенком точки — то есть не различались для
# всех, кто не различает красный и зелёный.
STATE_LABEL = {"ok": "норма", "warn": "увага", "bad": "збій", "off": "вимкнено"}

# Состояние -> цвет по смыслу. «Норма» цвета не получает: зелёный принадлежит
# деньгам, и раскрашенная им ферма из двадцати живых строк ничего не выделяет.
STATE_TONE = {"ok": "calm", "warn": "wait", "bad": "broken", "off": "off"}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def dot_html(state: str) -> str:
    """Цветная точка + слово. Оба, а не одно из двух."""
    st = state if state in STATE_LABEL else "off"
    return (f"<span class='dot {STATE_TONE[st]}'></span>"
            f"<span class='slab'>{esc(STATE_LABEL[st])}</span>")


def plural_dialogs(n: int) -> str:
    """«1 діалог», «2 діалоги», «5 діалогів» — иначе счётчик в модалке читается
    как машинный вывод ровно там, где человек принимает решение."""
    tail = abs(int(n)) % 100
    last = tail % 10
    if 11 <= tail <= 14:
        word = "діалогів"
    elif last == 1:
        word = "діалог"
    elif 2 <= last <= 4:
        word = "діалоги"
    else:
        word = "діалогів"
    return f"{n} {word}"


def leads_waiting(n: int) -> str:
    """«1 лід чекає», «2 ліди чекають», «5 лідів чекають» — ответ читается
    вслух, а не собирается из числа и существительного в именительном."""
    tail, last = abs(int(n)) % 100, abs(int(n)) % 10
    if 11 <= tail <= 14:
        word = "лідів"
    elif last == 1:
        word = "лід"
    elif 2 <= last <= 4:
        word = "ліди"
    else:
        word = "лідів"
    verb = "чекає" if (last == 1 and not 11 <= tail <= 14) else "чекають"
    return f"{n} {word} {verb} на вас"


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
    """Прошлого периода нет — так и сказано. Прочерк на месте дельты читался как
    «изменений нет», хотя сравнивать было не с чем вовсе."""
    if cur is None or prev in (None, 0):
        return "<div class='d na'>нема з чим порівняти</div>"
    d = (cur - prev) / prev * 100.0
    cls = "up" if d > 1 else ("down" if d < -1 else "flat")
    sign = "▲" if d > 1 else ("▼" if d < -1 else "•")
    return f"<div class='d {cls}'>{sign} {abs(d):.0f}%</div>"


def _drawable(s) -> bool:
    """Точек меньше двух — рисовать нечего. Линия из одной точки вырождается, а
    одинокий маркер в поле осей читается как «график есть»: человек видит
    оформление динамики там, где динамики нет."""
    return sum(1 for _, v in s.points if v is not None) >= 2


def line_chart(series: list, *, width: int = 390, height: int = 240) -> str:
    """SVG-график без библиотек.

    Деньги уходят на ПРАВУЮ ось и рисуются пунктиром (спека §6.2): 640 $ и
    3 оплаты на одной шкале превращают вторую линию в прямую по нулю. Пунктир —
    чтобы две оси нельзя было спутать формой.

    Система координат подобрана под ТЕЛЕФОН: viewBox масштабируется целиком, и
    подпись оси в 11 единиц при ширине 1000 превращалась на 390 px экрана в
    4 пикселя — то есть в узор. Ширина холста теперь близка к ширине телефона,
    а на десктопе картинку держит max-width.
    """
    if not series:
        return "<div class='empty'>Оберіть метрику вище.</div>"
    thin = [s for s in series if not _drawable(s)]
    series = [s for s in series if _drawable(s)]
    thin_note = (
        f"<div class='na'>{esc(', '.join(s.label for s in thin))}: "
        "точка одна, лінію не малюємо.</div>" if thin else "")
    if not series:
        return ("<div class='empty'>Недостатньо даних для графіка: точок менше "
                "двох. Одна точка — це не динаміка.</div>")

    pad_l, pad_r, pad_t, pad_b = 34, 38, 12, 22
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
    out = [f"<svg viewBox='0 0 {W} {H}' "
           "style='width:100%;max-width:560px;height:auto'>"]

    for i in range(5):                                   # сетка + левая ось
        y = pad_t + ih * i / 4
        out.append(f"<line x1='{pad_l}' y1='{y:.1f}' x2='{W-pad_r}' y2='{y:.1f}' "
                   f"stroke='#272c37' stroke-width='1'/>")
        v = lb[1] - (lb[1] - lb[0]) * i / 4
        # Мелкий диапазон с шагом 1.25 давал подписи 0,1,2,4,5 — деления
        # выглядели неравномерными. Ниже 8 по шкале печатаем десятые.
        fmt = f"{v:.1f}" if (lb[1] - lb[0]) < 8 else f"{v:.0f}"
        out.append(f"<text x='{pad_l-6}' y='{y+4:.1f}' fill='#98a2b3' font-size='12' "
                   f"text-anchor='end'>{fmt}</text>")
    if right:
        for i in range(5):
            y = pad_t + ih * i / 4
            v = rb[1] - (rb[1] - rb[0]) * i / 4
            rfmt = f"{v:.1f}" if (rb[1] - rb[0]) < 8 else f"{v:.0f}"
            out.append(f"<text x='{W-pad_r+6}' y='{y+4:.1f}' fill='#f0a92c' "
                       f"font-size='12'>{rfmt}</text>")

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
    return ("".join(out) + thin_note
            + f"<div style='margin-top:6px'>{legend}</div>")
