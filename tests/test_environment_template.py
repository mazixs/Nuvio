"""Проверки единого шаблона окружения и актуальных версий."""

from pathlib import Path

import pytest

import config


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_environment_template_contains_local_bot_api_credentials():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "TELEGRAM_API_ID=" in template
    assert "TELEGRAM_API_HASH=" in template
    assert "TELEGRAM_TOKEN=" in template
    assert "ADMIN_IDS=" in template
    assert "GOKAPI" not in template
    assert "python-dotenv" not in template
    assert "YTDLP_AUTO_UPDATE=false" in template


@pytest.mark.unit
def test_environment_template_documents_youtube_canary():
    """Канарейка выключена по умолчанию, а её ролик описан в шаблоне.

    Включает проверку владелец сам: это исходящий трафик и лишние обращения к
    YouTube с домашнего адреса. Значения в шаблоне обязаны совпадать с
    умолчаниями `config.py`, иначе оператор настроит не то, что получит.
    """
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    config_source = (ROOT / "config.py").read_text(encoding="utf-8")

    assert "CANARY_ENABLED=false" in template
    assert "CANARY_INTERVAL_HOURS=12" in template
    assert f"CANARY_VIDEO_ID={config.DEFAULT_CANARY_VIDEO_ID}" in template

    # Умолчания сверяются с парсерами, а не со значениями модуля: у владельца в
    # `.secrets/.env` канарейка может быть уже включена, и тест не должен от
    # этого краснеть.
    assert (
        'CANARY_ENABLED = _parse_bool(os.environ.get("CANARY_ENABLED"), default=False)'
        in config_source
    )
    assert config._parse_canary_interval_hours(None) == 12
    assert config._parse_canary_video_id(None) == config.DEFAULT_CANARY_VIDEO_ID
    # Мусор и слишком частая проверка молча заменяются безопасным умолчанием:
    # канарейка не должна падать из-за опечатки в настройке.
    assert config._parse_canary_interval_hours("каждый час") == 12
    assert config._parse_canary_interval_hours("0") == 12
    assert config._parse_canary_video_id("не id") == config.DEFAULT_CANARY_VIDEO_ID


@pytest.mark.unit
def test_direct_dependencies_are_pinned_to_reviewed_versions():
    requirements = (ROOT / "requirements.in").read_text(encoding="utf-8")

    expected = {
        "python-telegram-bot[job-queue]==22.8",
        "yt-dlp[default]==2026.8.18.122307.dev0",
        "curl_cffi==0.16.0",
        "httpx==0.28.1",
        "python-dotenv==1.2.2",
        "fastapi==0.141.1",
        "uvicorn[standard]==0.52.3",
        "jinja2==3.1.6",
        "itsdangerous==2.2.0",
        "python-multipart==0.0.32",
    }

    for dependency in expected:
        assert dependency in requirements


@pytest.mark.unit
def test_application_image_uses_python_314():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.14-slim@sha256:")
