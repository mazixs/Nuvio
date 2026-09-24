"""Проверки единого шаблона окружения и актуальных версий."""

import re
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
    assert "YTDLP_AUTO_UPDATE" not in template


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
    lock = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    direct = {}
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)(?:\[([A-Za-z0-9_,.-]+)\])?==([^\s]+)", line
        )
        assert match, f"Прямая зависимость не закреплена: {line}"
        name, extras, version = match.groups()
        name = name.lower().replace("_", "-")
        assert name not in direct, f"Повторная зависимость: {name}"
        direct[name] = (extras, version)

    assert set(direct) == {
        "python-telegram-bot", "yt-dlp", "curl-cffi", "httpx",
        "python-dotenv", "fastapi", "uvicorn", "jinja2",
        "itsdangerous", "python-multipart",
    }
    assert direct["python-telegram-bot"][0] == "job-queue"
    assert direct["yt-dlp"][0] == "default"
    assert direct["uvicorn"][0] == "standard"

    locked = {}
    for match in re.finditer(
        r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s\\]+)",
        lock,
        re.MULTILINE,
    ):
        name, version = match.groups()
        name = name.lower().replace("_", "-")
        assert name not in locked, f"Повторный pin в runtime lock-файле: {name}"
        locked[name] = version

    for name, (_, version) in direct.items():
        assert locked.get(name) == version, (
            f"Версия {name} не совпадает с runtime lock-файлом"
        )


@pytest.mark.unit
def test_application_image_uses_python_314():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.14-slim@sha256:")
