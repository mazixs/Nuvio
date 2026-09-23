"""Тесты конфигурации локального Telegram Bot API."""

import importlib.util
from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
LOCAL_API_URL = "http://telegram-bot-api:8081/bot"
LOCAL_FILE_URL = "http://telegram-bot-api:8081/file/bot"


def _load_config(monkeypatch: pytest.MonkeyPatch, **env: str):
    for name in (
        "TELEGRAM_LOCAL_MODE",
        "TELEGRAM_BOT_API_BASE_URL",
        "TELEGRAM_BOT_API_FILE_URL",
        "TELEGRAM_MAX_FILE_SIZE_MB",
        "TEMP_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    spec = importlib.util.spec_from_file_location("config_under_test", CONFIG_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_cloud_mode_keeps_50_mb_limit(monkeypatch, tmp_path):
    config = _load_config(
        monkeypatch,
        TEMP_DIR=str(tmp_path),
        TELEGRAM_MAX_FILE_SIZE_MB="2000",
    )

    assert config.TELEGRAM_LOCAL_MODE is False
    assert config.MAX_FILE_SIZE == 50 * 1024 * 1024


@pytest.mark.unit
def test_local_mode_uses_shared_directory_and_2_gb_limit(monkeypatch, tmp_path):
    config = _load_config(
        monkeypatch,
        TELEGRAM_LOCAL_MODE="true",
        TELEGRAM_BOT_API_BASE_URL=LOCAL_API_URL,
        TELEGRAM_BOT_API_FILE_URL=LOCAL_FILE_URL,
        TELEGRAM_MAX_FILE_SIZE_MB="2000",
        TEMP_DIR=str(tmp_path),
    )

    assert config.TELEGRAM_LOCAL_MODE is True
    assert config.TEMP_DIR == tmp_path.resolve()
    assert config.MAX_FILE_SIZE == 2000 * 1024 * 1024
    assert config.TELEGRAM_BOT_API_BASE_URL == LOCAL_API_URL
    assert config.TELEGRAM_BOT_API_FILE_URL == LOCAL_FILE_URL


@pytest.mark.unit
def test_local_limit_cannot_exceed_telegram_limit(monkeypatch, tmp_path):
    config = _load_config(
        monkeypatch,
        TELEGRAM_LOCAL_MODE="true",
        TELEGRAM_MAX_FILE_SIZE_MB="2500",
        TEMP_DIR=str(tmp_path),
    )

    assert config.MAX_FILE_SIZE == 2000 * 1024 * 1024


@pytest.mark.unit
def test_local_mode_rejects_cloud_api_urls(monkeypatch, tmp_path):
    config = _load_config(
        monkeypatch,
        TELEGRAM_LOCAL_MODE="true",
        TELEGRAM_BOT_API_BASE_URL="https://api.telegram.org/bot",
        TELEGRAM_BOT_API_FILE_URL="https://api.telegram.org/file/bot",
        TEMP_DIR=str(tmp_path),
    )
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "test-token")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_API_BASE_URL"):
        config.validate_config()
