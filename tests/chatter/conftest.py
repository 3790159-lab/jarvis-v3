"""Фикстуры security-тестов (P1P2 рев. 2).

Crypto рев. 2 требует entropy-файл (machine-scope DPAPI + optionalEntropy,
спека §2.1/§4.6): без него encrypt/decrypt падают явной ошибкой. Тестам,
которые просто пользуются crypto (secret_loader, wiring, recovery), даём
per-test entropy во временном файле через JARVIS_ENTROPY_FILE — живой
`.secrets/entropy.bin` не трогается никогда.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _test_entropy(tmp_path_factory, monkeypatch):
    """Каждому тесту — свой entropy-файл (изоляция: блоб одного теста
    не расшифруется в другом, как и задумано optionalEntropy). Живёт в
    ОТДЕЛЬНОЙ tmp-директории, не в tmp_path теста: тесты вида «рядом с
    .enc не появилось лишних файлов» не должны видеть entropy."""
    if sys.platform != "win32":
        yield None
        return
    from chatter.security import crypto

    path = tmp_path_factory.mktemp("entropy") / "entropy.bin"
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(path))
    crypto.generate_entropy(path)
    yield path
