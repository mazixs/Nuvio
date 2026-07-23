#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие фикстуры и конфигурация для всех тестов.

Pytest автоматически загружает этот файл перед запуском тестов.
"""

import sys
import types
from pathlib import Path

import pytest

# Добавляем корневую директорию проекта в PYTHONPATH
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Лёгкая заглушка yt_dlp, если библиотека не установлена в среде тестов.
if "yt_dlp" not in sys.modules:
    try:  # pragma: no cover - если установлен, пропускаем
        __import__("yt_dlp")  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - fallback только для CI без зависимостей
        stub_module = types.SimpleNamespace(
            YoutubeDL=None,
            utils=types.SimpleNamespace(DownloadError=Exception),
            cookies=types.SimpleNamespace(CookieLoadError=Exception),
        )
        sys.modules["yt_dlp"] = stub_module


# === МАРКЕРЫ ===


def pytest_configure(config):
    """Регистрация пользовательских маркеров."""
    config.addinivalue_line("markers", "syntax: тесты синтаксического анализа кода")
    config.addinivalue_line("markers", "unit: модульные тесты отдельных функций")
    config.addinivalue_line("markers", "integration: интеграционные тесты компонентов")


# === ОБЩИЕ ФИКСТУРЫ ===


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Корневая директория проекта."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def utils_dir(project_root: Path) -> Path:
    """Директория с утилитами."""
    return project_root / "utils"


@pytest.fixture(scope="session")
def tests_dir(project_root: Path) -> Path:
    """Директория с тестами."""
    return project_root / "tests"

