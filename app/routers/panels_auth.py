"""Owner-only гейт для обеих панелей.

Панели показывают переписку живых лидов (клиентский дашборд) и PID/ветки/сроки
ключей (панель Джарвиса). Это НЕ те данные, которые можно отдать «кому попало с
ключом от API», поэтому здесь отдельный уровень поверх общего
`auth_guard_middleware`: ключ обязан быть валидным И принадлежать владельцу.

РЕШЕНИЕ: гейт — явная FastAPI-зависимость на каждом роутере, а не доверие
middleware. Причина: middleware сегодня по умолчанию `off`
(JARVIS_AUTH_MIDDLEWARE_MODE), и панель, полагающаяся только на него, при
дефолтном конфиге открылась бы целиком. Зависимость закрыта по умолчанию и
открывается только явно заданным ключом.
"""
from __future__ import annotations

import html
import os
import secrets

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

_ENV_KEY = "JARVIS_PANELS_KEY"
_COOKIE = "panels_key"

# Куда разрешено уводить после входа. БЕЛЫЙ СПИСОК, а не проверка префикса:
# открытый редирект на ручке входа — это фишинг с собственного домена, и
# «начинается с /panel/» обходится строкой `/panel/../..`.
_NEXT_ALLOWED = ("/panel/tamapi", "/panel/jarvis")
_NEXT_DEFAULT = "/panel/tamapi"


def _expected() -> str:
    return (os.getenv(_ENV_KEY) or "").strip()


def _same(provided: str, expected: str) -> bool:
    """Постоянное по времени сравнение — по БАЙТАМ, а не по строкам.

    `secrets.compare_digest` на `str` требует ASCII и бросает TypeError на
    чём угодно другом. Со строками это значило, что ключ с кириллицей давал
    500 вместо 401: отказ, отличимый от обычного, — это подсказка тому, кто
    подбирает ключ, и шум в алертах для нас."""
    return secrets.compare_digest(
        provided.strip().encode("utf-8"), expected.encode("utf-8"))


def panels_enabled() -> bool:
    """Панели существуют, только если ключ задан. Без ключа роутеры не
    монтируются вовсе — «выключено» надёжнее, чем «включено, но защищено»."""
    return bool(_expected())


async def require_owner(
    request: Request,
    x_panels_key: str | None = Header(default=None, alias="X-Panels-Key"),
) -> None:
    """Ключ из заголовка или cookie. Cookie — чтобы браузер не требовал
    расширения для каждой картинки/фетча; она HttpOnly+SameSite=Strict и
    ставится единственной ручкой входа."""
    expected = _expected()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "panels disabled: JARVIS_PANELS_KEY not set")
    provided = x_panels_key or request.cookies.get(_COOKIE) or ""
    if not provided or not _same(provided, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")


COOKIE_NAME = _COOKIE

router = APIRouter(prefix="/panel", tags=["panels"])


def _safe_next(raw: str) -> str:
    """Куда уводить после входа. Белый список, а не проверка префикса."""
    return raw if raw in _NEXT_ALLOWED else _NEXT_DEFAULT


def _require_enabled() -> None:
    """Симметрия с `require_owner`: выключенная панель не имеет права оставить
    открытой хотя бы одну дверь — ни страницу формы, ни её приём."""
    if not _expected():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "panels disabled: JARVIS_PANELS_KEY not set")


_FORM_PAGE = """<!doctype html><html lang='uk'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='referrer' content='no-referrer'>
<title>Вхід до панелі</title><style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;
 justify-content:center;background:#0f1115;color:#e8e8ea;
 font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}}
form{{width:min(360px,90vw);padding:22px;border:1px solid #24262e;
 border-radius:14px;background:#161922}}
h1{{margin:0 0 14px;font-size:17px;font-weight:600}}
input{{width:100%;box-sizing:border-box;padding:13px;font-size:16px;
 border-radius:10px;border:1px solid #2c2f3a;background:#0f1115;color:#e8e8ea}}
button{{width:100%;margin-top:12px;padding:13px;font-size:16px;font-weight:600;
 border:0;border-radius:10px;background:#2f6df6;color:#fff}}
.err{{margin:0 0 12px;padding:9px 11px;border-radius:9px;
 background:#3a1418;color:#ff9a9a;font-size:14px}}
</style></head><body>
<form method='post' action='/panel/login' autocomplete='on'>
<h1>Вхід до панелі</h1>{error}
<input type='password' name='key' autocomplete='current-password'
 autocapitalize='off' autocorrect='off' spellcheck='false' autofocus
 placeholder='Ключ власника' aria-label='Ключ власника'>
<input type='hidden' name='next' value='{next}'>
<button type='submit'>Увійти</button>
</form></body></html>"""

_FORM_ERROR = "<p class='err'>Ключ не підійшов</p>"


def _form_html(target: str, *, error: bool = False) -> str:
    """Страница входа. Введённое значение на неё НЕ возвращается: отказ уедет и
    в скриншот, и в кэш браузера, а ключ в них попасть не должен."""
    return _FORM_PAGE.format(error=_FORM_ERROR if error else "",
                             next=html.escape(target, quote=True))


@router.get("")
async def panel_root(
    request: Request,
    x_panels_key: str | None = Header(default=None, alias="X-Panels-Key"),
):
    """Указатель на голом `/panel` — короткий адрес, который набирают с телефона.

    Заводится не для красоты: в корень API (`host:port`) промахивались дважды, и
    в истории браузера оседал голый адрес без пути — при следующем заходе он
    подставляется первым и ведёт мимо панели опять.

    Годность ключа сверяется, а не его НАЛИЧИЕ: протухшая cookie увела бы на
    панель, которая ответит 401, — тупик, из которого с телефона не выбраться
    иначе как чисткой cookie руками. Не годится — значит на форму.

    Ручка НЕ закрыта `require_owner` намеренно, как и форма входа: закрытый
    указатель отдаёт 401 вместо формы и перестаёт быть указателем. Показывать
    ему нечего — он только выбирает, куда увести."""
    _require_enabled()
    provided = x_panels_key or request.cookies.get(_COOKIE) or ""
    known = bool(provided) and _same(provided, _expected())
    return RedirectResponse(_NEXT_DEFAULT if known else "/panel/login",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
async def login_page(next: str = _NEXT_DEFAULT) -> HTMLResponse:
    """Дверь внутрь с телефона: ФОРМА, а не ссылка с ключом.

    Гейт принимает ключ заголовком ИЛИ cookie, но заголовок с мобильного
    браузера не отправить, поэтому cookie ставит отдельная дверь.

    Раньше дверь была ссылкой `?key=…`, и ключ оседал в четырёх местах разом:
    история браузера, адресная строка на скриншоте, лог любого прокси по пути
    и заголовок Referer. Форма шлёт тот же ключ телом POST — не остаётся ни
    одного следа. Параметр `key` здесь не читается ВООБЩЕ: пока он работал бы,
    старая ссылка из истории телефона оставалась бы действующей дверью.

    Ручка НЕ закрыта зависимостью `require_owner` намеренно: она и есть способ
    получить то, что зависимость проверяет."""
    _require_enabled()
    return HTMLResponse(_form_html(_safe_next(next)))


@router.post("/login")
async def login(request: Request):
    """Приём формы. Верный ключ → 303 и cookie; неверный → та же форма и 401.

    Тело разбирается `request.form()`, а не параметрами `Form(...)`: объявление
    `Form` требует установленного `python-multipart` В МОМЕНТ ИМПОРТА роутера, и
    на интерпретаторе без него падал бы весь бэкенд, а не одна эта дверь.
    Разбор urlencoded в starlette своей зависимости не имеет."""
    _require_enabled()
    form = await request.form()
    key = str(form.get("key") or "")
    target = _safe_next(str(form.get("next") or _NEXT_DEFAULT))

    if not key.strip() or not _same(key, _expected()):
        # 401, а не 422 на пустом поле: отличимый отказ подсказывает тому, кто
        # подбирает, что именно он сделал не так.
        return HTMLResponse(_form_html(target, error=True),
                            status_code=status.HTTP_401_UNAUTHORIZED)

    resp = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        _COOKIE, _expected(),
        httponly=True,          # ключ не должен доставаться скрипту на странице
        samesite="strict",      # чужой сайт не дёрнет панель от твоего имени
        path="/panel",          # ключ не поедет на остальные ручки бэкенда
        max_age=30 * 24 * 3600,
        # Secure ставим ТОЛЬКО поверх https: в тайлнете панель открывается по
        # http, и безусловный Secure сделал бы вход невозможным именно там, где
        # он и нужен. Внутри тайлнета канал шифрует сам Tailscale.
        secure=request.url.scheme == "https",
    )
    return resp
