"""Тесты рабочей копии cookie-файла.

yt-dlp перезаписывает файл, который ему отдали в `cookiefile`, и при записи
теряет сессионные cookies. Замерено на проде: один прогон убрал `YSC`, файл
похудел с 15 записей до 14. За недели работы от загруженного админом набора
осталась одна auth-cookie из шести — YouTube начал отвечать «Sign in to confirm
you're not a bot».

Поэтому yt-dlp получает не оригинал, а рабочую копию: пусть портит её.
"""

import os

import pytest

from utils.cookie_workfile import working_cookie_file


pytestmark = pytest.mark.unit

ORIGINAL = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tabc\n"


@pytest.fixture
def original(tmp_path):
    path = tmp_path / "www.youtube.com_cookies.txt"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path / "work"


def test_missing_original_gives_nothing(tmp_path, work_dir):
    assert working_cookie_file(tmp_path / "нет.txt", work_dir=work_dir) is None


def test_copy_repeats_the_original(original, work_dir):
    copy = working_cookie_file(original, work_dir=work_dir)

    assert copy is not None
    assert copy != original
    assert copy.read_text(encoding="utf-8") == ORIGINAL


def test_original_survives_what_ytdlp_does_to_the_copy(original, work_dir):
    """Ровно та защита, ради которой всё это существует."""
    copy = working_cookie_file(original, work_dir=work_dir)
    copy.write_text("# yt-dlp выкинул половину\n", encoding="utf-8")

    assert original.read_text(encoding="utf-8") == ORIGINAL


def test_second_call_keeps_the_rotated_cookies(original, work_dir):
    """YouTube обновляет cookies в ответах — затирать копию значит терять их."""
    copy = working_cookie_file(original, work_dir=work_dir)
    copy.write_text("# обновлённые cookies\n", encoding="utf-8")

    again = working_cookie_file(original, work_dir=work_dir)

    assert again == copy
    assert again.read_text(encoding="utf-8") == "# обновлённые cookies\n"


def test_freshly_uploaded_original_replaces_the_copy(original, work_dir):
    """Админ загрузил новый файл — рабочая копия обязана уступить."""
    copy = working_cookie_file(original, work_dir=work_dir)
    copy.write_text("# устаревшее\n", encoding="utf-8")

    original.write_text("# новый набор от админа\n", encoding="utf-8")
    os.utime(original, (copy.stat().st_mtime + 10, copy.stat().st_mtime + 10))

    again = working_cookie_file(original, work_dir=work_dir)

    assert again.read_text(encoding="utf-8") == "# новый набор от админа\n"


def test_copy_is_readable_only_by_owner(original, work_dir):
    """В файле живая сессия аккаунта — режим обязан совпадать с оригиналом."""
    copy = working_cookie_file(original, work_dir=work_dir)

    assert copy.stat().st_mode & 0o077 == 0
