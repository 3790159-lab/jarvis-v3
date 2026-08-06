# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — сборка сервисов манифеста (грязный слой).

Здесь ловится главная ловушка детектора: `Get-Process` отдаёт только имя
образа, и все наши сервисы выглядят как «python» и «powershell». Отличить бота
от гардиана можно ТОЛЬКО по командной строке, поэтому сборщик берёт
`Win32_Process` (read-only) и ищет по имени скрипта.

Вторая ловушка — наследство 29.07: `c:/jarvis` является ПРЕФИКСОМ
`c:/jarvis_worktrees/...`, и раннер из чужого worktree однажды уже сошёл за
боевой. Здесь это дало бы ложное «сервис жив» — то есть ПРОПУЩЕННУЮ дыру,
самый дорогой вид ошибки для сторожа.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import autonomy_snapshot as snap


class FakeRun:
    """Фейк `subprocess.run`: отвечает по подстроке в команде."""

    def __init__(self, by_marker: dict[str, str]):
        self.by_marker = by_marker
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for marker, payload in self.by_marker.items():
            if marker in joined:
                return SimpleNamespace(stdout=payload, stderr="", returncode=0)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)


MANIFEST = {
    "services": [
        {"id": "telegram-bot", "owner": "WindowsScheduledTask:JarvisBotGuardian",
         "environment": "windows", "criticality": "critical",
         "entrypoint": "python tools/jarvis_smart_telegram_control.py",
         "production_enabled": True},
        {"id": "cloudflare-tunnel", "owner": "WindowsService:cloudflared",
         "environment": "windows", "criticality": "high",
         "entrypoint": "cloudflared tunnel run jarvis-desktop",
         "production_enabled": True},
        {"id": "n8n-main", "owner": "DockerCompose:jarvis-n8n",
         "environment": "docker", "criticality": "high",
         "entrypoint": "n8n_docker_strong/docker-compose.yml#n8n-main",
         "production_enabled": True},
        {"id": "ollama", "owner": "OptionalLocalProcess:ollama",
         "environment": "windows", "criticality": "optional",
         "entrypoint": "ollama serve", "production_enabled": False},
    ]
}


def _manifest(tmp_path, payload=MANIFEST):
    path = tmp_path / "jarvis_services.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _collect(tmp_path, *, processes=(), services=(), payload=MANIFEST):
    return {s.service_id: s for s in snap.collect_services(
        _manifest(tmp_path, payload), processes=processes,
        windows_services=services)}


# --- чистый разбор entrypoint ------------------------------------------------

def test_probe_key_is_the_script_name_for_a_python_entrypoint():
    assert snap.entrypoint_probe_key(
        "python tools/jarvis_smart_telegram_control.py") == "jarvis_smart_telegram_control.py"


def test_probe_key_is_the_script_name_for_a_powershell_entrypoint():
    assert snap.entrypoint_probe_key(
        "powershell.exe -File scripts/bot_guardian_detached.ps1") == "bot_guardian_detached.ps1"


def test_entrypoint_without_a_script_has_no_probe_key():
    """`cloudflared tunnel run ...` и `docker-compose.yml#n8n-main` искать в
    командных строках бессмысленно — пробу даёт не entrypoint."""
    assert snap.entrypoint_probe_key("cloudflared tunnel run jarvis-desktop") is None
    assert snap.entrypoint_probe_key(
        "n8n_docker_strong/docker-compose.yml#n8n-main") is None


# --- сборка сервисов ---------------------------------------------------------

def test_service_is_alive_when_its_script_is_in_a_command_line(tmp_path):
    bot = _collect(tmp_path, processes=[
        {"ProcessId": 11248, "Name": "python.exe",
         "CommandLine": r"python C:\jarvis\tools\jarvis_smart_telegram_control.py"},
    ])["telegram-bot"]

    assert bot.probe == "process"
    assert bot.probe_key == "jarvis_smart_telegram_control.py"
    assert bot.process_present is True


def test_service_is_dead_when_nothing_matches(tmp_path):
    bot = _collect(tmp_path, processes=[
        {"ProcessId": 4, "Name": "python.exe", "CommandLine": "python -m http.server"},
        {"ProcessId": 5, "Name": "notepad.exe", "CommandLine": None},
    ])["telegram-bot"]

    assert bot.process_present is False


def test_copy_running_from_a_worktree_does_not_count_as_alive(tmp_path):
    """Грабля 29.07: `c:/jarvis` — ПРЕФИКС `c:/jarvis_worktrees/...`.
    Зачесть чужую копию за боевую значит пропустить настоящую дыру."""
    bot = _collect(tmp_path, processes=[
        {"ProcessId": 999, "Name": "python.exe",
         "CommandLine": r"python C:\jarvis_worktrees\panels\tools\jarvis_smart_telegram_control.py"},
    ])["telegram-bot"]

    assert bot.process_present is False


def test_docker_service_is_unprobed_not_dead(tmp_path):
    n8n = _collect(tmp_path)["n8n-main"]

    assert n8n.probe == "docker"
    assert n8n.process_present is None


def test_windows_service_liveness_comes_from_the_service_list(tmp_path):
    running = _collect(tmp_path, services=[
        {"Name": "cloudflared", "Status": "Running"}])["cloudflare-tunnel"]
    stopped = _collect(tmp_path, services=[
        {"Name": "cloudflared", "Status": "Stopped"}])["cloudflare-tunnel"]

    assert running.probe == "windows_service" and running.probe_key == "cloudflared"
    assert running.process_present is True
    assert stopped.process_present is False


def test_live_service_status_arrives_as_an_enum_number(tmp_path):
    """🔴 Найдено ЖИВЫМ прогоном 06.08, а не тестом: `Get-Service | Select
    Status | ConvertTo-Json` отдаёт `{"Status":4}` — enum сериализуется
    ЧИСЛОМ. Мой первый тест кормил выдуманную строку "Running", и детектор
    объявил РАБОТАЮЩИЙ cloudflared мёртвым. 4 = Running, 1 = Stopped."""
    running = _collect(tmp_path, services=[
        {"Name": "cloudflared", "Status": 4}])["cloudflare-tunnel"]
    stopped = _collect(tmp_path, services=[
        {"Name": "cloudflared", "Status": 1}])["cloudflare-tunnel"]

    assert running.process_present is True
    assert stopped.process_present is False


def test_service_absent_from_the_service_list_is_unknown_not_dead(tmp_path):
    """Найдено мутацией: если `Get-Service` ничего не сказал про службу (список
    не разобрался, служба не установлена), «не знаем» обязано остаться `None`.
    Вариант «мертва» дал бы ложную карточку на каждом прогоне."""
    unknown = _collect(tmp_path, services=[
        {"Name": "spooler", "Status": "Running"}])["cloudflare-tunnel"]

    assert unknown.process_present is None


def test_deliberate_disable_is_carried_from_the_manifest_as_is(tmp_path):
    assert _collect(tmp_path)["ollama"].production_enabled is False


def test_missing_manifest_yields_no_services_not_a_crash(tmp_path):
    """Клон Codex — чужая песочница: его может не быть на машине вовсе."""
    assert snap.collect_services(tmp_path / "нет-такого.json",
                                 processes=(), windows_services=()) == []


def test_broken_manifest_yields_no_services_not_a_crash(tmp_path):
    path = tmp_path / "jarvis_services.json"
    path.write_text("{не json", encoding="utf-8")

    assert snap.collect_services(path, processes=(), windows_services=()) == []


def test_full_snapshot_carries_services(tmp_path):
    (tmp_path / "scripts").mkdir()
    run = FakeRun({"Win32_Process": json.dumps([
        {"ProcessId": 1, "Name": "python.exe",
         "CommandLine": r"python C:\jarvis\tools\jarvis_smart_telegram_control.py"}])})

    snapshot = snap.collect(tmp_path, now=1_000.0, run=run,
                            manifest_path=_manifest(tmp_path))

    assert {s.service_id for s in snapshot["services"]} == {
        "telegram-bot", "cloudflare-tunnel", "n8n-main", "ollama"}
