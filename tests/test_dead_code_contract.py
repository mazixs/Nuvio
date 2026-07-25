"""Контракт отсутствия подтверждённого мёртвого кода."""

import dataclasses
from pathlib import Path

import pytest

import messages
from utils import telegram_utils, tiktok_fast_path
from utils.video_cache import CachedVideo


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_url_handoff_leftovers_are_removed():
    """Передачи файла по URL в sendVideo в коде нет — остатки не нужны.

    URL_HANDOFF_LIMIT_BYTES, fits_url_handoff и FastMedia.cover использовались
    только тестами, а CLAUDE.md при этом обещал «проверку лимита передачи
    по URL», которой в поведении бота не существует.
    """
    assert not hasattr(tiktok_fast_path, "URL_HANDOFF_LIMIT_BYTES")
    assert not hasattr(tiktok_fast_path, "fits_url_handoff")

    field_names = {
        field.name for field in dataclasses.fields(tiktok_fast_path.FastMedia)
    }
    assert "cover" not in field_names

    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "лимита передачи по URL" not in guide


def test_obsolete_message_constants_are_removed():
    obsolete_names = {
        "TOO_LARGE_FILE_MESSAGE",
        "ERROR_YTDLP",
        "ERROR_GENERIC",
        "ERROR_SENDING_FILE",
        "AUDIO_ONLY_LABEL",
        "VIDEO_ONLY_LABEL",
        "COMBINED_LABEL",
    }

    assert obsolete_names.isdisjoint(vars(messages))


def test_obsolete_cache_helpers_are_removed():
    assert not hasattr(telegram_utils, "_try_send_cached")
    assert not hasattr(CachedVideo, "to_dict")


def test_unused_pytest_network_scaffolding_is_removed():
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    pytest_ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")

    assert "--run-slow" not in conftest
    assert "--run-network" not in conftest
    assert "network:" not in pytest_ini
    assert "slow:" not in pytest_ini
