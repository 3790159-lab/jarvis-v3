# -*- coding: utf-8 -*-
"""Ш1: детектор «register-скрипт без живого таска» + фильтр ложных.

Разведка 2026-08-06 дала пять кандидатов, и ровно два из них — НЕ дыры:

    register_morning_digest.ps1        -> JarvisMorningDigest        ДЫРА
    register_error_digest.ps1          -> JarvisErrorDigest          ДЫРА
    register_state_backup.ps1          -> JarvisStateBackup          ДЫРА
    register_telegram_webhook.ps1      -> таска не регистрирует      НЕ дыра
    register_ig_schedule_publisher.ps1 -> очереди не существует      НЕ дыра

Если детектор не умеет их различать, теневая неделя стартует с 2 мусорных
предложений из 5 = 40% шума при гейте 20%, и Ш2 не откроется никогда. Поэтому
тест на НЕ-срабатывание здесь не менее важен, чем на срабатывание.
"""
from __future__ import annotations

from app.services import autonomy_detectors as det
from app.services import autonomy_proposals as ap

MORNING = det.RegisterScript(
    script="scripts/register_morning_digest.ps1",
    task_name="JarvisMorningDigest", task_present=False, feature_active=True)
ERRORS = det.RegisterScript(
    script="scripts/register_error_digest.ps1",
    task_name="JarvisErrorDigest", task_present=False, feature_active=True)
BACKUP = det.RegisterScript(
    script="scripts/register_state_backup.ps1",
    task_name="JarvisStateBackup", task_present=False, feature_active=True)
WEBHOOK = det.RegisterScript(
    script="scripts/register_telegram_webhook.ps1",
    task_name=None, task_present=False, feature_active=True)
IG = det.RegisterScript(
    script="scripts/register_ig_schedule_publisher.ps1",
    task_name="JarvisIgSchedulePublisher", task_present=False,
    feature_active=False, inactive_reason="очередь state/ig_scheduled_posts.json не существует")
LIVE = det.RegisterScript(
    script="scripts/register_bot_guardian.ps1",
    task_name="JarvisBotGuardian", task_present=True, feature_active=True)

ALL_FIVE = [MORNING, ERRORS, BACKUP, WEBHOOK, IG]


def _snapshot(scripts):
    return {"register_scripts": list(scripts)}


def test_fires_on_every_real_gap():
    found = det.detect_register_scripts_without_task(_snapshot(ALL_FIVE))
    assert [p.subject for p in found] == [
        "JarvisMorningDigest", "JarvisErrorDigest", "JarvisStateBackup"]


def test_does_not_fire_on_a_script_that_registers_no_task():
    """telegram_webhook переключает бота на webhook через Bot API — таск он
    не создаёт вовсе, и «нет таска» для него не факт, а бессмыслица."""
    found = det.detect_register_scripts_without_task(_snapshot([WEBHOOK]))
    assert found == []


def test_does_not_fire_on_a_dormant_feature():
    """ig_schedule_publisher: очереди на диске нет, публиковать нечего.
    Автоматизировать спящую фичу — это шум, а не автономность."""
    found = det.detect_register_scripts_without_task(_snapshot([IG]))
    assert found == []


def test_does_not_fire_when_the_task_is_alive():
    assert det.detect_register_scripts_without_task(_snapshot([LIVE])) == []


def test_empty_snapshot_is_not_an_error():
    assert det.detect_register_scripts_without_task({}) == []
    assert det.detect_register_scripts_without_task(_snapshot([])) == []


def test_noise_share_of_this_detector_is_zero_on_real_data():
    """Прямая проверка гейта 20% на фактических данных разведки."""
    found = det.detect_register_scripts_without_task(_snapshot(ALL_FIVE))
    false_positives = [p for p in found
                       if p.subject in {"JarvisIgSchedulePublisher", None}]
    assert not false_positives
    assert len(found) == 3


def test_evidence_is_stable_across_runs():
    """Контракт хранилища: в evidence только СТАБИЛЬНЫЕ факты. Два прогона
    над одним состоянием обязаны дать один хеш, иначе каждый прогон плодит
    «новое наблюдение» и шум растёт линейно во времени."""
    first = det.detect_register_scripts_without_task(_snapshot(ALL_FIVE))
    second = det.detect_register_scripts_without_task(_snapshot(ALL_FIVE))
    assert [ap.evidence_hash(p.evidence) for p in first] == \
           [ap.evidence_hash(p.evidence) for p in second]


def test_proposal_is_structural_not_prose():
    found = det.detect_register_scripts_without_task(_snapshot([MORNING]))[0]
    assert found.kind == "register_script_without_task"
    assert found.proposed_action == {
        "action": "register_scheduled_task",
        "script": "scripts/register_morning_digest.ps1",
        "task": "JarvisMorningDigest"}
    assert found.evidence["script"] == "scripts/register_morning_digest.ps1"
    assert found.evidence["task_present"] is False


def test_action_level_comes_from_the_policy_and_fails_closed():
    """Уровень читается из политики (levels 0..4). Неизвестное действие —
    4, а не «наверное можно»."""
    policy = {"scheduled_task_mutation": 4, "report": 0}
    assert det.resolve_action_level("scheduled_task_mutation", policy) == 4
    assert det.resolve_action_level("report", policy) == 0
    assert det.resolve_action_level("never_heard_of_it", policy) == 4
    assert det.resolve_action_level("report", {}) == 4


def test_registering_a_task_is_level_four_by_the_current_policy():
    """Находка, которую обязан зафиксировать тест: регистрация таска
    попадает в `scheduled_task_mutation` = уровень 4 (запрещено). То есть
    планируемый первый класс исполнителя Ш3 действующая политика НЕ
    разрешает — это решение владельца, а не деталь реализации."""
    found = det.detect_register_scripts_without_task(_snapshot([MORNING]))[0]
    assert found.action_level == 4


def test_detector_writes_nothing_by_itself():
    """Чистая функция: ни БД, ни сети, ни файлов, ни подпроцессов.

    Проверяем ИМПОРТЫ разбором AST, а не подстрокой: первая версия этого
    теста краснела на слове `telegram` в докстринге — сторож, врущий про
    здоровый код, хуже отсутствующего.
    """
    import ast

    with open(det.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"urllib", "requests", "httpx", "socket", "smtplib",
                 "sqlite3", "subprocess", "os", "shutil", "pathlib"}
    assert not (imported & forbidden), f"детектор тянет {imported & forbidden}"
    calls = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in calls, "детектор читает файлы — снимок обязан приходить аргументом"
