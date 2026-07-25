"""Проверки соответствия документации фактическому поведению кода."""

import inspect
from pathlib import Path

import pytest

from utils import cache_commands


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_adr_001_records_fast_path_supersession():
    """ADR-001 отклонил снижение качества, а быстрый путь его реализует.

    Отклонённая альтернатива «540p H.264 вместо HEVC» стала поведением по
    умолчанию (576×1024 H.264), поэтому ADR обязан это фиксировать вместе с
    датой решения и ссылкой на замеры.
    """
    adr = (
        ROOT / "docs" / "technical" / "adr-001-tiktok-audio-hevc-compatibility.md"
    ).read_text(encoding="utf-8")

    assert "2026-07-25" in adr
    assert "TIKTOK_FAST_PATH" in adr
    assert "576×1024" in adr
    assert "latency-disk-network-research.md" in adr
    # Требование ADR-001 остаётся в силе для обоих путей скачивания.
    assert "H.264" in adr


@pytest.mark.unit
def test_fast_path_flag_documents_cache_reset():
    """Смена флага не влияет на уже закэшированные ссылки — это надо сказать.

    Кэш file_id читается до скачивания и живёт 90 дней, поэтому после
    TIKTOK_FAST_PATH=false прежние URL продолжат отдавать 576×1024.
    """
    documents = {
        ".env.example": (ROOT / ".env.example").read_text(encoding="utf-8"),
        "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
        "AGENTS.md": (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
    }

    for name, text in documents.items():
        assert "TIKTOK_FAST_PATH" in text, name
        assert "кэш" in text.lower(), name
        assert "/cleanup_cache" in text, f"{name}: нет упоминания команды кэша"
        # /cleanup_cache снимает только просроченные записи, поэтому честный
        # способ немедленного откатa — удаление файла кэша.
        assert "telegram_cache.db" in text, f"{name}: нет способа откатить кэш"


@pytest.mark.unit
def test_cleanup_cache_command_only_removes_expired_records():
    """Команда, на которую ссылается документация, должна делать ровно то.

    /cleanup_cache вызывает cleanup_expired(ttl_days=90), то есть удаляет лишь
    записи старше TTL и НЕ сбрасывает свежие ссылки быстрого пути. Документация
    обязана описывать это без преувеличения.
    """
    assert hasattr(cache_commands, "cleanup_cache_command")

    source = inspect.getsource(cache_commands.cleanup_cache_command)
    assert "cleanup_expired(ttl_days=90)" in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'CommandHandler("cleanup_cache", cleanup_cache_command)' in main_source
