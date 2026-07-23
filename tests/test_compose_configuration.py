"""Структурные тесты Compose для локального Telegram Bot API."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_compose_has_local_bot_api_and_shared_media():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "telegram-bot-api:" in compose
    assert "TELEGRAM_API_ID" in compose
    assert "TELEGRAM_API_HASH" in compose
    assert "TELEGRAM_LOCAL_MODE: \"true\"" in compose
    assert "shared-media:/app/media" in compose
    assert "8081:8081" not in compose
    assert "${NUVIO_ENV_FILE:-.secrets/.env}" in compose


@pytest.mark.unit
def test_local_api_source_revision_is_pinned():
    dockerfile = (ROOT / "Dockerfile.telegram-bot-api").read_text(
        encoding="utf-8"
    )

    assert "adfd7f6a8e990272851777eeb3ae0def4216f161" in dockerfile
    assert "github.com/tdlib/telegram-bot-api.git" in dockerfile


@pytest.mark.unit
def test_development_override_builds_nuvio_from_source():
    compose = (ROOT / "compose.dev.yaml").read_text(encoding="utf-8")

    assert "build: ." in compose
    assert "nuvio:dev" in compose


@pytest.mark.unit
def test_legacy_compose_files_are_removed():
    assert not (ROOT / "docker-compose.yml").exists()
    assert not (ROOT / "docker-compose.prod.yml").exists()
