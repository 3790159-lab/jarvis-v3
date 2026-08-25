# -*- coding: utf-8 -*-
"""DEV-77: клиент без реквизитов — ЗАКОННОЕ состояние, а не ежедневная авария.

ЧТО МЕНЯЕТСЯ. `client_sets` искал `chatter/clients/<slug>/requisites.yaml` у
КАЖДОГО включённого клиента и называл его пропажей у того, у кого платежи
выключены. Замер 25.08: `JarvisStateBackup` отвечает `rc = 1` каждую ночь из-за
Ярины (`payments.enabled: false`). Красное каждый день приучает не смотреть —
и настоящая пропажа реквизитов у платящего клиента утонет в том же шуме.

ФОРМА B (решение владельца 25.08): ожидание НЕ объявляется вторым числом в
реестре, а ВЫВОДИТСЯ из того, что уже объявлено, — `payments.enabled` в
`chatter/clients/<slug>/settings.yaml`. Второго источника правды не заводится,
поэтому расхождение двух списков невозможно по построению
([[jarvis-two-numbers-for-one-thing]]).

ГЛАВНОЕ ЗДЕСЬ — НЕ «НЕ ИСКАТЬ ФАЙЛ», А СВЕРКА ОЖИДАНИЯ С ДИСКОМ (спека §5):

    ждём + есть      -> норма, объект едет
    ждём + нет       -> `missing`, как сегодня
    не ждём + нет    -> ТИХО, объекта в отчёте нет вовсе
    не ждём + ЕСТЬ   -> ГРОМКОЕ расхождение И файл всё равно едет

Четвёртая строка — весь смысл файла. Клиенту включили платежи, реквизиты
появились, `settings.yaml` правили, а бэкап о них не знает: без этой строки
платёжные данные перестали бы ездить МОЛЧА — объект вне списка не `missing`,
он невидим (DEV-70). Файл при расхождении ЕДЕТ: сохранность дороже чистоты
отчёта, потерять сегодняшнюю копию ради правильной формулировки нельзя.

НЕИЗВЕСТНОСТЬ ТРАКТУЕТСЯ В СТОРОНУ ОЖИДАНИЯ. Нет `settings.yaml`, нет ключа,
файл не читается — ждём файл, то есть остаёмся при сегодняшнем громком
поведении. Обратное умолчание («не знаем — значит не ждём») выключало бы
бэкап реквизитов у любого клиента, чей конфиг не дочитался.

ЧЕГО ЗДЕСЬ НЕТ. Ни сети, ни живого `.secrets/`: деревья строятся в `tmp_path`.
Единственное обращение к боевому дереву — §7, сверка вывода с тем, что о
платежах думает сам чаттер: пин в ОБЕ стороны, чтобы предикат не разъехался с
модулем, из которого он выведен.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import state_backup as sb

ALPHA = "alpha"   # платящий: payments.enabled: true
BETA = "beta"     # без платежей: payments.enabled: false

REGISTRY = """
clients:
  alpha:
    enabled: true
    personas: [alpha]
  beta:
    enabled: true
    personas: [beta]
"""


def _tree(tmp_path: Path, *, alpha_pays=True, beta_pays=False,
          alpha_req=True, beta_req=False,
          alpha_settings=True, beta_settings=True) -> Path:
    """Дерево репозитория с двумя включёнными клиентами.

    ДВА клиента, а не один, и в РАЗНЫХ состояниях: реализация, написанная под
    одного, легко оказывается верной для него и слепой для второго.
    """
    root = tmp_path / "repo"
    for slug, db in ((ALPHA, True), (BETA, True)):
        if db:
            p = root / ".secrets" / f"{slug}.db"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"db {slug}", encoding="utf-8")
    for slug, pays, has_req, has_settings in (
            (ALPHA, alpha_pays, alpha_req, alpha_settings),
            (BETA, beta_pays, beta_req, beta_settings)):
        d = root / "chatter" / "clients" / slug
        d.mkdir(parents=True, exist_ok=True)
        if has_settings:
            (d / "settings.yaml").write_text(
                "persona_name: x\npayments:\n  enabled: %s\n"
                % ("true" if pays else "false"), encoding="utf-8")
        if has_req:
            (d / "requisites.yaml").write_text("iban: XX", encoding="utf-8")
    reg = root / "chatter" / "clients" / "registry.yaml"
    reg.write_text(REGISTRY, encoding="utf-8")
    return root


def _by_slug(sets, slug):
    for s in sets:
        if s.slug == slug:
            return s
    return None


# ── §1. предикат ожидания ───────────────────────────────────────────────────
def test_payments_on_means_the_file_is_expected(tmp_path):
    root = _tree(tmp_path)
    assert sb.expects_requisites(root, ALPHA) is True


def test_payments_off_means_the_file_is_not_expected(tmp_path):
    root = _tree(tmp_path)
    assert sb.expects_requisites(root, BETA) is False


@pytest.mark.parametrize("what", ["нет файла", "нет ключа", "не yaml"])
def test_unknown_is_read_as_expected_not_as_absent(tmp_path, what):
    """«Не знаем» обязано читаться как «ждём».

    Обратное умолчание выключило бы бэкап реквизитов у клиента, чей конфиг
    просто не дочитался, — и сделало бы это молча.
    """
    root = _tree(tmp_path, beta_settings=False)
    d = root / "chatter" / "clients" / BETA
    if what == "нет ключа":
        (d / "settings.yaml").write_text("persona_name: x\n", encoding="utf-8")
    elif what == "не yaml":
        (d / "settings.yaml").write_text("::: не yaml :::\n[", encoding="utf-8")
    assert sb.expects_requisites(root, BETA) is True, (
        "неизвестность прочитана как «файла ждать не надо» — "
        "реквизиты платящего клиента перестанут ездить молча")


@pytest.mark.parametrize("raw", ['"false"', '""', "0", "null", "[]"])
def test_the_flag_must_be_a_real_boolean(tmp_path, raw):
    """Не-`bool` — это НЕИЗВЕСТНОСТЬ, а не значение тумблера.

    🔴 Мутационный гейт вскрыл здесь дыру: сторож проверял только `"false"`, а
    на ней `bool(flag)` даёт ТОТ ЖЕ ответ, что и правильная ветка, — то есть
    подмена «не-bool → ждём» на «не-bool → как получится» проходила молча.
    Опасны как раз ЛОЖНЫЕ не-bool (`""`, `0`, `null`, `[]`): с ними подмена
    отвечает «не ждём», и реквизиты платящего клиента перестают ездить.
    Поэтому список значений — литеральный и с обеих сторон истинности.
    """
    root = _tree(tmp_path, beta_settings=False)
    d = root / "chatter" / "clients" / BETA
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.yaml").write_text(
        f"payments:\n  enabled: {raw}\n", encoding="utf-8")
    assert sb.expects_requisites(root, BETA) is True


# ── §2. четыре состояния сверки ─────────────────────────────────────────────
def test_expected_and_present_travels(tmp_path):
    root = _tree(tmp_path)
    cs = _by_slug(sb.client_sets(root, registry_text=REGISTRY), ALPHA)
    assert cs.requisites is not None
    assert cs.missing == ()
    assert cs.undeclared == ()


def test_expected_and_absent_is_still_named(tmp_path):
    """Платящий клиент без реквизитов — по-прежнему ГРОМКО."""
    root = _tree(tmp_path, alpha_req=False)
    cs = _by_slug(sb.client_sets(root, registry_text=REGISTRY), ALPHA)
    assert cs.requisites is None
    assert any("requisites.yaml" in m for m in cs.missing), cs.missing


def test_not_expected_and_absent_is_silent(tmp_path):
    """Ровно тот случай, ради которого арка: Ярина и ежедневный rc=1."""
    root = _tree(tmp_path)
    cs = _by_slug(sb.client_sets(root, registry_text=REGISTRY), BETA)
    assert cs.requisites is None
    assert cs.missing == (), (
        "клиент без платежей опять числится пропажей — задача продолжит "
        "падать каждую ночь на законном состоянии")
    assert cs.undeclared == ()


def test_not_expected_but_present_is_loud_AND_still_travels(tmp_path):
    """Четвёртое состояние. Обе половины обязательны.

    Реализация, которая только кричит, теряет сегодняшнюю копию платёжных
    данных; которая только копирует — молчит о том, что конфиг разъехался.
    """
    root = _tree(tmp_path, beta_req=True)
    cs = _by_slug(sb.client_sets(root, registry_text=REGISTRY), BETA)
    assert cs.requisites is not None, "файл на диске есть, а в бэкап не поехал"
    assert any("requisites.yaml" in u for u in cs.undeclared), cs.undeclared
    assert cs.missing == ()


def test_the_other_client_is_untouched_in_every_state(tmp_path):
    """Правка не имеет права менять вердикт соседа ни в одном состоянии."""
    for beta_req in (False, True):
        for alpha_req in (False, True):
            root = _tree(tmp_path / f"t{beta_req}{alpha_req}",
                         alpha_req=alpha_req, beta_req=beta_req)
            cs = _by_slug(sb.client_sets(root, registry_text=REGISTRY), ALPHA)
            assert (cs.requisites is not None) is alpha_req
            assert (cs.missing == ()) is alpha_req
            assert cs.undeclared == ()


# ── §3. расхождение доезжает до владельца ───────────────────────────────────
def test_result_carries_the_contradiction_and_is_not_ok():
    r = sb.BackupResult(date="2026-08-25")
    assert r.ok, "чистый результат обязан быть ok"
    r.contradictions.append("beta: requisites.yaml есть, платежи выключены")
    assert not r.ok, (
        "расхождение реестра с диском не портит вердикт — значит задача "
        "вернёт rc 0 и владелец о нём не узнает")


def test_summary_names_slug_path_and_what_to_do():
    r = sb.BackupResult(date="2026-08-25")
    r.contradictions.append(
        "beta: chatter/clients/beta/requisites.yaml есть на диске, "
        "а payments.enabled: false — файл УЕХАЛ, поправьте settings.yaml")
    text = sb.format_client_backup_result(r)
    assert "beta" in text
    assert "requisites.yaml" in text
    assert "УЕХАЛ" in text, "сводка не говорит, что файл всё-таки сохранён"


# ── §4. живое дерево: предикат не разъехался с чаттером ─────────────────────
LIVE_ROOT = Path(__file__).resolve().parents[1]


def _live_slugs() -> list[str]:
    from chatter.core.client_registry import parse_registry
    reg = LIVE_ROOT / "chatter" / "clients" / "registry.yaml"
    if not reg.is_file():
        return []
    return [e.slug for e in parse_registry(reg.read_text(encoding="utf-8"))
            if e.enabled]


@pytest.mark.parametrize("slug", _live_slugs() or ["_нет_реестра_"])
def test_the_predicate_agrees_with_what_chatter_thinks(slug):
    """Пин в ОБЕ стороны на боевом реестре.

    Предикат ВЫВЕДЕН из `payments.enabled`, но выведен он ЗДЕСЬ, отдельным
    чтением. Если завтра чаттер начнёт брать тумблер иначе, вывод разъедется
    с ним молча — а разъехавшись, снова начнёт либо ронять задачу каждую ночь,
    либо тихо не бэкапить реквизиты. Сторож называет расхождение в тот день,
    когда оно случится.
    """
    if slug == "_нет_реестра_":
        pytest.skip("боевого реестра нет — чужое окружение")
    import yaml
    p = LIVE_ROOT / "chatter" / "clients" / slug / "settings.yaml"
    if not p.is_file():
        pytest.skip(f"у {slug} нет settings.yaml в этом дереве")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    enabled = (raw.get("payments") or {}).get("enabled")
    expected = enabled is True
    assert sb.expects_requisites(LIVE_ROOT, slug) is expected, (
        f"вывод про реквизиты у `{slug}` разошёлся с payments.enabled")
