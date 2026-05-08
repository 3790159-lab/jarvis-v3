from __future__ import annotations

import ast
import datetime as dt
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    tool: str
    summary: str
    details: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class Calculator:
    ALLOWED_AST = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.USub,
        ast.UAdd,
        ast.Load,
        ast.Constant,
    )

    def calculate(self, text: str) -> ToolResult | None:
        text_norm = self._normalize_text(text)

        triangle = self._extract_triangle_sides(text_norm)
        if triangle:
            a, b, c = triangle
            if not self._is_valid_triangle(a, b, c):
                return ToolResult(
                    ok=False,
                    tool="calculator",
                    summary="Такой треугольник не существует.",
                    details="Нарушено неравенство треугольника.",
                    facts={"sides": [a, b, c]},
                )

            s = (a + b + c) / 2
            area = math.sqrt(s * (s - a) * (s - b) * (s - c))
            return ToolResult(
                ok=True,
                tool="calculator",
                summary=f"Площадь треугольника равна {area:.2f}.",
                details=(
                    f"Использована формула Герона: s = {s:.2f}, "
                    f"S = √(s(s-a)(s-b)(s-c)) = {area:.4f}."
                ),
                facts={"area": round(area, 4), "sides": [a, b, c], "formula": "heron"},
            )

        expr = self._extract_expression(text_norm)
        if expr:
            try:
                value = self._safe_eval(expr)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    tool="calculator",
                    summary=f"Не удалось вычислить выражение: {exc}",
                    details=f"Выражение: {expr}",
                )
            return ToolResult(
                ok=True,
                tool="calculator",
                summary=f"Результат: {value}.",
                details=f"Вычислено выражение: {expr}",
                facts={"expression": expr, "value": value},
            )

        sqrt_expr = self._extract_sqrt_expression(text_norm)
        if sqrt_expr:
            try:
                value = math.sqrt(self._safe_eval(sqrt_expr))
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    tool="calculator",
                    summary=f"Не удалось вычислить корень: {exc}",
                    details=f"Подкоренное выражение: {sqrt_expr}",
                )
            return ToolResult(
                ok=True,
                tool="calculator",
                summary=f"Результат: {round(value, 6)}.",
                details=f"Вычислен корень из выражения: {sqrt_expr}",
                facts={"expression": f"sqrt({sqrt_expr})", "value": round(value, 6)},
            )

        return None

    def _normalize_text(self, text: str) -> str:
        return (
            text.lower()
            .replace("×", "*")
            .replace("х", "*")
            .replace("x", "*")
            .replace(":", "/")
            .replace(",", ".")
        )

    def _is_valid_triangle(self, a: float, b: float, c: float) -> bool:
        return a + b > c and a + c > b and b + c > a

    def _extract_triangle_sides(self, text: str) -> tuple[float, float, float] | None:
        if "треуголь" not in text:
            return None
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if len(nums) < 3:
            return None
        vals = [float(x) for x in nums[:3]]
        return vals[0], vals[1], vals[2]

    def _extract_expression(self, text: str) -> str | None:
        for prefix in ("сколько будет", "посчитай", "вычисли"):
            if text.startswith(prefix):
                expr = text[len(prefix):].strip()
                normalized = self._normalize_expression(expr)
                return normalized or None

        m = re.search(r"умнож[а-я]*\s+(\d+(?:\.\d+)?)\s+на\s+(\d+(?:\.\d+)?)", text)
        if m:
            return f"{m.group(1)} * {m.group(2)}"

        m = re.search(r"подел[а-я]*\s+(\d+(?:\.\d+)?)\s+на\s+(\d+(?:\.\d+)?)", text)
        if m:
            return f"{m.group(1)} / {m.group(2)}"

        m = re.search(r"слож[а-я]*\s+(\d+(?:\.\d+)?)\s+и\s+(\d+(?:\.\d+)?)", text)
        if m:
            return f"{m.group(1)} + {m.group(2)}"

        m = re.search(r"вычт[а-я]*\s+(\d+(?:\.\d+)?)\s+из\s+(\d+(?:\.\d+)?)", text)
        if m:
            return f"{m.group(2)} - {m.group(1)}"

        candidate = self._normalize_expression(text)
        if candidate and re.search(r"[\+\-\*/]", candidate) and re.fullmatch(r"[0-9\.\+\-\*\/\(\) ]+", candidate):
            return candidate
        return None

    def _extract_sqrt_expression(self, text: str) -> str | None:
        m = re.search(r"корень\s+из\s+(.+)$", text)
        if not m:
            return None
        expr = self._normalize_expression(m.group(1))
        return expr or None

    def _normalize_expression(self, expr: str) -> str:
        expr = expr.replace("плюс", "+").replace("минус", "-")
        expr = re.sub(r"[^0-9\.\+\-\*\/\(\) ]", " ", expr)
        expr = re.sub(r"\s+", " ", expr).strip()
        return expr

    def _safe_eval(self, expr: str) -> float:
        if not expr:
            raise ValueError("пустое выражение")
        node = ast.parse(expr, mode="eval")
        for subnode in ast.walk(node):
            if not isinstance(subnode, self.ALLOWED_AST):
                raise ValueError("недопустимое выражение")
        value = eval(compile(node, "<expr>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
        if isinstance(value, (int, float)):
            return round(float(value), 6)
        raise ValueError("результат не является числом")


class WeatherTool:
    CITY_ALIASES = {
        "киев": "Kyiv",
        "киеве": "Kyiv",
        "киева": "Kyiv",
        "київ": "Kyiv",
        "києв": "Kyiv",
        "києві": "Kyiv",
        "києва": "Kyiv",
        "kyiv": "Kyiv",
        "kiev": "Kyiv",
        "львов": "Lviv",
        "львове": "Lviv",
        "львів": "Lviv",
        "львові": "Lviv",
        "lviv": "Lviv",
        "одесса": "Odesa",
        "одессе": "Odesa",
        "одеса": "Odesa",
        "одесі": "Odesa",
        "odesa": "Odesa",
        "odessa": "Odesa",
    }

    WEATHER_CODES = {
        0: "ясно",
        1: "преимущественно ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "изморозь",
        51: "слабая морось",
        53: "морось",
        55: "сильная морось",
        61: "слабый дождь",
        63: "дождь",
        65: "сильный дождь",
        71: "слабый снег",
        73: "снег",
        75: "сильный снег",
        80: "слабый ливень",
        81: "ливень",
        82: "сильный ливень",
        95: "гроза",
    }

    def fetch(self, text: str) -> ToolResult:
        location = self._extract_location(text) or "Kyiv"
        coords = self._geocode(location)
        if not coords:
            return ToolResult(ok=False, tool="weather", summary=f"Не удалось найти локацию: {location}")

        lat, lon, resolved_name = coords
        lower = text.lower()
        if "выходн" in lower:
            return self._weekend_forecast(lat, lon, resolved_name)
        return self._current_weather(lat, lon, resolved_name)

    def _extract_location(self, text: str) -> str | None:
        lower = text.lower()

        for alias, canonical in self.CITY_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", lower):
                return canonical

        match = re.search(r"(?:в|по)\s+([A-Za-zА-Яа-яІіЇїЄєҐґ\- ]{2,})$", text.strip())
        if not match:
            return None

        raw = match.group(1).strip()
        normalized = raw.lower()
        return self.CITY_ALIASES.get(normalized, raw)

    def _json_get(self, base_url: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{base_url}?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": "YA2/2.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _geocode(self, place: str) -> tuple[float, float, str] | None:
        data = self._json_get(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": place, "count": 1, "language": "ru", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            return None
        item = results[0]
        return float(item["latitude"]), float(item["longitude"]), str(item.get("name") or place)

    def _current_weather(self, lat: float, lon: float, place: str) -> ToolResult:
        data = self._json_get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
        )
        cur = data.get("current") or {}
        temp = cur.get("temperature_2m")
        wind = cur.get("wind_speed_10m")
        code = cur.get("weather_code")
        desc = self.WEATHER_CODES.get(code, "неизвестная погода")
        return ToolResult(
            ok=True,
            tool="weather",
            summary=f"Сейчас в {place}: {temp}°C, ветер {wind} км/ч, {desc}.",
            details="Ответ собран по данным weather tool без свободного пересказа модели.",
            facts={
                "location": place,
                "temperature_c": temp,
                "wind_kmh": wind,
                "weather_code": code,
                "weather_desc": desc,
                "mode": "current",
            },
            raw=data,
        )

    def _weekend_forecast(self, lat: float, lon: float, place: str) -> ToolResult:
        today = dt.date.today()
        days_until_sat = (5 - today.weekday()) % 7
        sat = today + dt.timedelta(days=days_until_sat)
        sun = sat + dt.timedelta(days=1)

        data = self._json_get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
                "timezone": "auto",
            },
        )

        daily = data.get("daily") or {}
        times = daily.get("time") or []
        out: list[str] = []
        facts: dict[str, Any] = {"location": place, "mode": "weekend", "days": []}

        for target in (sat.isoformat(), sun.isoformat()):
            if target not in times:
                continue
            idx = times.index(target)
            day_info = {
                "date": target,
                "max_c": daily["temperature_2m_max"][idx],
                "min_c": daily["temperature_2m_min"][idx],
                "wind_kmh": daily["wind_speed_10m_max"][idx],
            }
            facts["days"].append(day_info)
            out.append(
                f"{target}: {day_info['min_c']}…{day_info['max_c']}°C, ветер до {day_info['wind_kmh']} км/ч"
            )

        if not out:
            return ToolResult(ok=False, tool="weather", summary="Не удалось собрать прогноз на выходные.")

        return ToolResult(
            ok=True,
            tool="weather",
            summary=f"Прогноз на выходные для {place}: " + " | ".join(out),
            details="Ответ собран по данным weather tool без свободного пересказа модели.",
            facts=facts,
            raw=data,
        )


class LocalProjectTools:
    def __init__(self, allowed_roots: list[str]) -> None:
        self.allowed_roots = [Path(x).resolve() for x in allowed_roots if x]

    def run(self, text: str, default_root: str) -> ToolResult:
        root = Path(default_root).resolve()
        if not self._allowed(root):
            return ToolResult(ok=False, tool="local_tools", summary="Папка проекта не входит в разрешённые пути.")

        lower = text.lower()
        if "git status" in lower:
            return self._git_status(root)
        if "какие агенты" in lower:
            return self._list_agents(root)
        if "покажи файлы" in lower or "структур" in lower or "файлы проекта" in lower:
            return self._list_files(root)
        if "найди" in lower:
            return self._search_file(root, text)
        if "прочитай" in lower or "readme" in lower:
            return self._read_file(root, text)
        return self._list_files(root)

    def _allowed(self, root: Path) -> bool:
        for base in self.allowed_roots:
            try:
                root.relative_to(base)
                return True
            except ValueError:
                continue
        return False

    def _list_files(self, root: Path) -> ToolResult:
        try:
            names = sorted(p.name for p in root.iterdir())
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, tool="local_tools", summary=f"Не удалось прочитать папку проекта: {exc}")

        preview = ", ".join(names[:20])
        return ToolResult(
            ok=True,
            tool="local_tools",
            summary=f"В корне проекта найдено {len(names)} элементов: {preview}.",
            facts={"items": names},
        )

    def _search_file(self, root: Path, text: str) -> ToolResult:
        match = re.search(r"найди[: ]+([A-Za-z0-9_\-\.]+)", text, flags=re.I)
        target = match.group(1) if match else ""
        if not target:
            return ToolResult(ok=False, tool="local_tools", summary="Не понял, какой файл искать.")

        try:
            matches = [
                str(p.relative_to(root))
                for p in root.rglob("*")
                if p.is_file() and target.lower() in p.name.lower()
            ]
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, tool="local_tools", summary=f"Поиск не выполнился: {exc}")

        if not matches:
            return ToolResult(ok=False, tool="local_tools", summary=f"Файл по запросу '{target}' не найден.")

        return ToolResult(
            ok=True,
            tool="local_tools",
            summary="Найдены файлы: " + ", ".join(matches[:20]),
            facts={"matches": matches},
        )

    def _read_file(self, root: Path, text: str) -> ToolResult:
        candidates = ["README.md", ".env", ".env.example", "app/telegram_bot.py", "app/orchestrator.py"]
        requested = next((c for c in candidates if Path(c).name.lower() in text.lower()), "README.md")
        path = root / requested
        if not path.exists():
            return ToolResult(ok=False, tool="local_tools", summary=f"Файл {requested} не найден.")

        try:
            snippet = path.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, tool="local_tools", summary=f"Не удалось прочитать {requested}: {exc}")

        return ToolResult(
            ok=True,
            tool="local_tools",
            summary=f"Содержимое {requested}:",
            details=snippet,
            facts={"path": requested},
        )

    def _git_status(self, root: Path) -> ToolResult:
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, tool="local_tools", summary=f"git status не выполнился: {exc}")

        output = (proc.stdout or proc.stderr or "").strip() or "Рабочее дерево чистое."
        return ToolResult(ok=True, tool="local_tools", summary="Результат git status:", details=output)

    def _list_agents(self, root: Path) -> ToolResult:
        agent_file = root / "app" / "agent_registry.py"
        if not agent_file.exists():
            return ToolResult(ok=False, tool="local_tools", summary="agent_registry.py не найден.")

        try:
            text = agent_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, tool="local_tools", summary=f"Не удалось прочитать agent_registry.py: {exc}")

        names = re.findall(r'register\(\s*[\'"]([^\'"]+)[\'"]', text)
        if not names:
            names = re.findall(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", text)

        if not names:
            return ToolResult(ok=False, tool="local_tools", summary="Не удалось извлечь список агентов.")

        return ToolResult(
            ok=True,
            tool="local_tools",
            summary="Обнаружены агенты/классы: " + ", ".join(names[:20]),
            facts={"agents": names},
        )


class ToolExecutor:
    def __init__(self, allowed_roots: list[str]) -> None:
        self.calc = Calculator()
        self.weather = WeatherTool()
        self.local = LocalProjectTools(allowed_roots)

    def run(self, plan_route: str, text: str, *, project_root: str, trace_text: str = "") -> ToolResult:
        if plan_route == "trace":
            return ToolResult(ok=True, tool="trace", summary=trace_text)

        if plan_route == "math_first":
            result = self.calc.calculate(text)
            if result:
                return result
            return ToolResult(ok=False, tool="calculator", summary="Не удалось распознать математическую задачу.")

        if plan_route == "facts_first":
            return self.weather.fetch(text)

        if plan_route == "local_first":
            return self.local.run(text, project_root)

        return ToolResult(ok=False, tool="none", summary="Для этого route нет tool-исполнителя.")