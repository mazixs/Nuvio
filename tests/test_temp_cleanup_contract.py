"""Проверки очистки временных файлов без удаления активной загрузки."""

import os
import time

from utils import temp_file_manager as temp


def test_stale_cleanup_preserves_active_and_recent_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(temp, "TEMP_DIR", tmp_path)
    old = tmp_path / "old"
    active = tmp_path / "active"
    recent = tmp_path / "recent"
    for directory in (old, active, recent):
        directory.mkdir()
        (directory / "video.part").write_bytes(b"media")
    old_time = time.time() - 48 * 60 * 60
    for directory in (old, active):
        os.utime(directory, (old_time, old_time))

    removed, failed = temp.cleanup_stale_temp_files(active_sessions={"active"})

    assert (removed, failed) == (1, 0)
    assert not old.exists()
    assert (active / "video.part").exists()
    assert (recent / "video.part").exists()


def test_cleanup_one_failure_does_not_stop_other_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(temp, "TEMP_DIR", tmp_path)
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    original = temp.shutil.rmtree

    def remove(path):
        if path == bad:
            raise PermissionError("занят")
        return original(path)

    monkeypatch.setattr(temp.shutil, "rmtree", remove)

    removed, failed = temp.cleanup_temp_files()

    assert (removed, failed) == (1, 1)
    assert bad.exists()
    assert not good.exists()


def test_stale_cleanup_continues_after_permission_error(monkeypatch, tmp_path):
    monkeypatch.setattr(temp, "TEMP_DIR", tmp_path)
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    old_time = time.time() - 48 * 60 * 60
    os.utime(bad, (old_time, old_time))
    os.utime(good, (old_time, old_time))
    original = temp.shutil.rmtree

    def remove(path):
        if path == bad:
            raise PermissionError("занят")
        return original(path)

    monkeypatch.setattr(temp.shutil, "rmtree", remove)

    assert temp.cleanup_stale_temp_files() == (1, 1)
    assert bad.exists() and not good.exists()


def test_session_cleanup_only_removes_given_session(monkeypatch, tmp_path):
    monkeypatch.setattr(temp, "TEMP_DIR", tmp_path)
    first = temp.get_temp_file_path("first", "a.mp4")
    second = temp.get_temp_file_path("second", "b.mp4")
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    assert temp.cleanup_temp_files("first") == (1, 0)
    assert not first.exists() and second.exists()
