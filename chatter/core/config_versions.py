"""Версионирование конфига клиента (config-арка §4).

Каждое изменение → снимок 4 файлов в `<client_dir>/.versions/<int(ts)>/`.
`/rollback` откатывает на предыдущий снимок; стартовый fail-safe грузит
последний хороший снимок, если текущий файл битый. Клиент сломал базу знаний
в пятницу — откатывается сам, без звонка разработчику.

Чистые файловые операции, ноль Telethon/сети — тестируется на tmp_path.
"""
from __future__ import annotations

import shutil
from pathlib import Path

CONFIG_FILES = ("persona.md", "knowledge.md", "playbook.md", "settings.yaml",
                "examples.yaml")
_VERSIONS_DIR = ".versions"
_DEFAULT_KEEP = 20


def _versions_root(client_dir: Path) -> Path:
    return Path(client_dir) / _VERSIONS_DIR


def list_versions(client_dir: Path) -> list[Path]:
    """Каталоги версий по возрастанию ts (имя = int(ts))."""
    root = _versions_root(client_dir)
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
    return sorted(dirs, key=lambda p: int(p.name))


def latest_version(client_dir: Path) -> Path | None:
    vs = list_versions(client_dir)
    return vs[-1] if vs else None


def previous_version(client_dir: Path) -> Path | None:
    """Предпоследний снимок (для /rollback: «на шаг назад»). None если версий < 2."""
    vs = list_versions(client_dir)
    return vs[-2] if len(vs) >= 2 else None


def _content(dir_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in CONFIG_FILES:
        p = Path(dir_path) / f
        out[f] = p.read_text(encoding="utf-8") if p.exists() else ""
    return out


def snapshot(client_dir: Path, *, now: float, keep: int = _DEFAULT_KEEP) -> Path | None:
    """Снять снимок 4 файлов. Пропускаем, если контент идентичен ПОСЛЕДНЕМУ
    снимку (не плодим дубли). Подрезаем до `keep` самых свежих. Возвращает
    каталог версии или None (если пропущено)."""
    client_dir = Path(client_dir)
    latest = latest_version(client_dir)
    if latest is not None and _content(latest) == _content(client_dir):
        return None
    # Микросекундное имя: несколько изменений в одну СЕКУНДУ (типично в проде при
    # правке через пульт и в тестах) не должны схлопываться в один каталог,
    # иначе теряется история и /rollback остаётся без предыдущей версии.
    dest = _versions_root(client_dir) / str(int(now * 1_000_000))
    dest.mkdir(parents=True, exist_ok=True)
    for f in CONFIG_FILES:
        src = client_dir / f
        if src.exists():
            shutil.copy2(src, dest / f)
    _prune(client_dir, keep)
    return dest


def _prune(client_dir: Path, keep: int) -> None:
    vs = list_versions(client_dir)
    for old in vs[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)


def restore(client_dir: Path, version_dir: Path) -> None:
    """Скопировать файлы из снимка ПОВЕРХ текущих файлов клиента."""
    client_dir, version_dir = Path(client_dir), Path(version_dir)
    for f in CONFIG_FILES:
        src = version_dir / f
        if src.exists():
            shutil.copy2(src, client_dir / f)
