# План: сторож на дрейф префикса brain (§9 спеки `2026-08-19-classifier-on-haiku.md`)

Ветка: `arc/brain-prefix-drift-guard`, worktree `C:\jarvis_worktrees\prefix-drift`.
Мержится ОТДЕЛЬНО от Хайку (§9.6). N = 15% — решение владельца 19.08.

---

## 0. Что защищаем

Величина, от которой зависит цена (размер стабильного префикса brain), выросла
×1.34 за месяц молча, и полосы съехали 790 → 653 диал/мес. Сторож обязан
кричать, когда она выросла больше чем на N% против **записанного человеком**
эталона. Самообновляющийся эталон догонял бы дрейф и всегда молчал — это
главный запрет раздела (§9.2, гейт Д3).

---

## 1. ПУБЛИЧНЫЙ КОНТРАКТ (его же видит автор сторожей)

### 1.1. Новый модуль `chatter/core/prefix_budget.py`

```python
DEFAULT_BASELINES_PATH: Path      # <repo>/chatter/prefix_baselines.yaml

class PrefixBaselineError(Exception):
    """Файл эталонов сломан ЦЕЛИКОМ (не читается / не той формы)."""

@dataclass(frozen=True)
class Baseline:
    slug: str
    brain_tokens: int
    measured_with: str            # модель, КОТОРОЙ снят эталон
    measured_on: str              # ISO-дата, для человека

@dataclass(frozen=True)
class PrefixVerdict:
    slug: str
    check: str                    # "brain_drift"
    ok: bool                      # True = в пределах эталона
    loud: bool                    # True = обязан быть слышен владельцу
    fatal: bool                   # ВСЕГДА False в этой ветке (§9.2 «не отказ»)
    kind: str                     # within | drift | no_baseline | measure_failed
    actual: int | None
    baseline: int | None
    threshold_percent: int
    message: str                  # человеческий текст, уже с числами

def load_baselines(path=None) -> tuple[dict[str, Baseline], int]: ...
def count_tokens(model: str, system_text: str, *, client=None) -> int: ...
def brain_drift_verdict(cfg, *, baselines, threshold_percent,
                        counter=count_tokens) -> PrefixVerdict: ...
def check_client_prefixes(cfg, *, counter=count_tokens,
                          baselines_path=None) -> list[PrefixVerdict]: ...
```

### 1.2. Семантика, по которой пишутся сторожа

Предел: `limit = baseline * (100 + N) // 100` (целочисленный пол).

| условие | kind | ok | loud | fatal |
|---|---|---|---|---|
| `actual <= limit` | `within` | True | False | False |
| `actual > limit` | `drift` | False | **True** | False |
| эталона для слага нет | `no_baseline` | False | **True** | False |
| `counter` бросил исключение | `measure_failed` | False | **True** | False |

* `brain_drift_verdict` **НИКОГДА не бросает** (DEV-18: сбой не глотаем молча —
  он кричит вердиктом `measure_failed`, но старт клиента не роняет, §9.5).
* Ни одна функция модуля **НИКОГДА не пишет** в файл эталонов (гейт Д3).
* Считать токены обязана **модель эталона** (`Baseline.measured_with`), а не
  `cfg.settings.model`. Обоснование — §2.0 спеки: токенизаторы sonnet и haiku
  расходятся на 7–12%, и замер другой моделью сдвинул бы всё сравнение на
  величину порядка самого порога. Дрейф — ОТНОШЕНИЕ, поэтому единственное
  требование к модели счёта: она должна совпадать с той, которой снят эталон.
* Текст `message` обязан называть: слаг, фактическое число, эталон, порог N%,
  предел, и — для `no_baseline` — путь файла эталонов и то, что вносит его
  ЧЕЛОВЕК.

### 1.3. Файл эталонов `chatter/prefix_baselines.yaml`

```yaml
threshold_percent: 15
clients:
  volska:  {brain: 9131,  measured_with: claude-sonnet-5, measured_on: "2026-08-19"}
  yarina:  {brain: 14847, measured_with: claude-sonnet-5, measured_on: "2026-08-19"}
  demo:    {brain: 2394,  measured_with: claude-sonnet-5, measured_on: "2026-08-19"}
```

Числа — из §9.3 спеки, БЕЗ пересчёта. Шапка файла обязана крупно сказать, что
строку поднимает человек осознанной правкой, и почему автообновление запрещено.

Битый/непрочитанный файл → `PrefixBaselineError` из `load_baselines`;
`check_client_prefixes` его ловит и отдаёт `measure_failed`-подобный громкий
вердикт, а не падает.

---

## 2. Точки вызова

### 2.1. Старт клиента — `chatter/telethon_run.py`

* `load_personas`: после `_build_llm`, **только если `isinstance(llm, AnthropicLLM)`**
  — в fake-режиме денег нет, сети нет и сторожу нечего защищать (тот же
  критерий, по которому уже гейтится `_bind_classifier`).
* `PersonaBundle` получает поле `prefix_findings: tuple[str, ...] = ()` — тексты
  громких вердиктов этой персоны.
* `build_runner`: собрать по всем персонам в `runner._prefix_findings`
  (по образцу `runner._startup_recovery`, строка ~2007).
* `_on_connected`: если непусто — `await runner._notify_owner_notice(
  cfg_text("prefix_drift", lang, detail=...))`. Ключ `prefix_drift` добавить в
  ТРИ словаря `chatter/core/console.py` (ru/en/uk), как у `cfg_startup_recovered`.
* `reload_configs`: громкие вердикты — в `log.warning`. Алерт на перечитывании
  в эту ветку НЕ входит (решение записано в §4 плана).

### 2.2. CLI-путь — `chatter/run.py`

В `main()`, после `_build_llm`, при реальном клиенте: печатать громкие вердикты
в stdout строкой `[chatter] ⚠️ ...`. Не роняет.

### 2.3. `--check` онбординга — `chatter/onboard/checks.py`

* `CHECK_IDS` → `C1…C16`; `C16` добавляется в `FLAG_IDS` (флаг, не красное —
  §9.2 «не отказ»).
* `run_checks(client_dir, report_document, *, slug, token_counter=None)` —
  счётчик **инъектируется**; сеть в проверках по умолчанию запрещена.
* `token_counter is None` → C16 = флаг с текстом, начинающимся «ЗАМЕР НЕ
  ВЫПОЛНЕН» (счётчик не задан). Так офлайн-прогоны остаются совместимы, а
  молчания нет: строка видна в отчёте.
* C16 берёт конфиг из уже готового `ctx.config` (живой `Config`), префикс — из
  `chatter.core.brain.build_system_prompt(ctx.config)`.
* `chatter/onboard/__main__.py` передаёт настоящий счётчик, если в окружении
  есть `ANTHROPIC_API_KEY`; иначе печатает громкую строку «замер префикса не
  выполнен: нет ключа».
* Обновить в `checks.py` docstring «Ровно 15 вердиктов» и всё, что считает 15.

---

## 3. Гейты ветки (§9.6)

| # | гейт |
|---|---|
| Д1 | сторож краснеет (`kind="drift"`, `loud=True`) на префиксе, раздутом на N%+1 |
| Д2 | сторож молчит (`kind="within"`, `loud=False`) на префиксе в пределах эталона, включая РОВНО предел |
| Д3 | файл эталонов побайтово не изменился после срабатывания сторожа |
| Д4 | сбой `count_tokens` не роняет `load_personas` и даёт громкий `measure_failed` |
| Д5 | полный `pytest tests/` не хуже базлайна + мутационный гейт (основная сессия) |

---

## 4. Решения, где спека молчала

1. **Модель счёта берётся из эталона, а не из конфига клиента.** §9.3 снял все
   три числа sonnet'ом, включая demo, у которого brain работает на haiku.
   Сверять haiku-замер с sonnet-эталоном значило бы получить +10% «дрейфа» на
   ровном месте — две трети порога из воздуха. Дрейф это отношение, поэтому
   важно не «правильная модель», а «та же модель, что у эталона».
2. **Алерт только на СТАРТЕ, на перечитывании — лог.** §9.5 называет две точки:
   старт и `--check`. `reload_configs` — синхронный, notifier там доступен не
   везде, и расширять радиус в ветке-предохранителе не будем.
3. **Сторож не работает в fake-режиме LLM.** Без реального клиента нет ни
   сети, ни денег; иначе каждый офлайн-тест ходил бы в API.
4. **Отсутствие эталона — громко, но не отказ.** Новый клиент без записанной
   строки — ровно та дыра, ради которой раздел ставится; но §9.2 запрещает
   отказ, поэтому громкость, а не смерть старта.
