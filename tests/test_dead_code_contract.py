"""Контракт отсутствия подтверждённого мёртвого кода."""

from pathlib import Path

import messages
from utils import telegram_utils
from utils.video_cache import CachedVideo


ROOT = Path(__file__).resolve().parents[1]


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
