"""Загрузчики обязаны отдавать yt-dlp рабочую копию, а не оригинал cookies.

Сам модуль `cookie_workfile` бесполезен, пока хоть один загрузчик передаёт
`cookiefile` с путём к файлу, который загрузил админ: yt-dlp перезапишет его и
потеряет сессионные cookies.
"""

import pytest

from utils import tiktok_instagram_utils, youtube_utils
from utils.cookie_workfile import WORK_DIR_NAME


pytestmark = pytest.mark.unit

CONTENT = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "cookies.txt"
    path.write_text(CONTENT, encoding="utf-8")
    return path


def _is_working_copy(path, original) -> bool:
    resolved = str(path)
    return (
        resolved != str(original)
        and WORK_DIR_NAME in resolved
        and open(resolved, encoding="utf-8").read() == CONTENT
    )


def test_youtube_hands_over_a_working_copy(cookie_file, monkeypatch):
    monkeypatch.setattr(youtube_utils, "YOUTUBE_COOKIES_FILE", str(cookie_file))

    assert _is_working_copy(
        youtube_utils._cookiefile_if_available(True), cookie_file
    )


def test_youtube_without_cookies_stays_without_them(cookie_file, monkeypatch):
    monkeypatch.setattr(youtube_utils, "YOUTUBE_COOKIES_FILE", str(cookie_file))

    assert youtube_utils._cookiefile_if_available(False) is None


def test_tiktok_hands_over_a_working_copy(cookie_file, monkeypatch):
    monkeypatch.setattr(tiktok_instagram_utils, "TIKTOK_COOKIES_FILE", cookie_file)

    assert _is_working_copy(
        tiktok_instagram_utils._tiktok_cookiefile(True), cookie_file
    )


def test_instagram_hands_over_a_working_copy(cookie_file, monkeypatch):
    monkeypatch.setattr(tiktok_instagram_utils, "INSTAGRAM_COOKIES_FILE", cookie_file)

    assert _is_working_copy(
        tiktok_instagram_utils._instagram_cookiefile(True), cookie_file
    )


@pytest.mark.parametrize(
    "helper",
    ["_tiktok_cookiefile", "_instagram_cookiefile"],
)
def test_missing_cookies_give_nothing(tmp_path, monkeypatch, helper):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    absent = tmp_path / "нет.txt"
    monkeypatch.setattr(tiktok_instagram_utils, "TIKTOK_COOKIES_FILE", absent)
    monkeypatch.setattr(tiktok_instagram_utils, "INSTAGRAM_COOKIES_FILE", absent)

    assert getattr(tiktok_instagram_utils, helper)(True) is None
