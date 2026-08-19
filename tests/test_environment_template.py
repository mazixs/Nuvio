"""Проверки единого шаблона окружения и актуальных версий."""

from pathlib import Path

import pytest


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
