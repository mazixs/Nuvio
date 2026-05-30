#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты линтинга ruff.

Запускает `ruff check` через pytest, чтобы линтинг был частью
единого тестового набора и запускался локально через `pytest -m syntax`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent


@pytest.mark.syntax
def test_ruff_check() -> None:
    """Проверяет что весь проект проходит линтинг ruff без ошибок."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"ruff check обнаружил ошибки (exit code {result.returncode}):\n\n"
            f"{result.stdout}\n"
            f"{result.stderr}"
        )
