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
if 'yt_dlp' not in sys.modules:
    try:  # pragma: no cover - если установлен, пропускаем
        __import__("yt_dlp")  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - fallback только для CI без зависимостей
        stub_module = types.SimpleNamespace(
            YoutubeDL=None,
            utils=types.SimpleNamespace(DownloadError=Exception),
            cookies=types.SimpleNamespace(CookieLoadError=Exception),
        )
        sys.modules['yt_dlp'] = stub_module


# === МАРКЕРЫ ===

def pytest_configure(config):
    """Регистрация пользовательских маркеров."""
    config.addinivalue_line(
        "markers", "syntax: тесты синтаксического анализа кода"
    )
    config.addinivalue_line(
        "markers", "unit: модульные тесты отдельных функций"
    )
    config.addinivalue_line(
        "markers", "integration: интеграционные тесты компонентов"
    )
    config.addinivalue_line(
        "markers", "slow: медленные тесты (пропускаются по умолчанию)"
    )
    config.addinivalue_line(
        "markers", "network: тесты, требующие интернет-соединения"
    )


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


# === ХУКИ ===

def pytest_collection_modifyitems(config, items):
    """Модификация собранных тестов.
    
    Добавляет маркер 'slow' для тестов, которые могут выполняться долго.
    """
    for item in items:
        # Автоматически помечаем network тесты как slow
        if "network" in item.keywords:
            item.add_marker(pytest.mark.slow)


def pytest_addoption(parser):
    """Добавление пользовательских опций командной строки."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Запустить медленные тесты"
    )
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Запустить тесты, требующие интернет-соединения"
    )


def pytest_runtest_setup(item):
    """Настройка перед запуском каждого теста.
    
    Пропускает тесты с определенными маркерами, если не указаны флаги.
    """
    if "slow" in item.keywords and not item.config.getoption("--run-slow"):
        pytest.skip("Пропускаем медленный тест (используйте --run-slow)")
    
    if "network" in item.keywords and not item.config.getoption("--run-network"):
        pytest.skip("Пропускаем сетевой тест (используйте --run-network)")
