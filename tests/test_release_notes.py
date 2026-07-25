"""Тесты генерации release notes для тега `v*`."""

import pytest

from scripts.release_notes import classify_subject, render_notes


@pytest.mark.unit
def test_subject_prefix_decides_section():
    assert classify_subject("feat: быстрый путь TikTok") == "feat"
    assert classify_subject("fix: не кэшировать транзиентный сбой") == "fix"
    assert classify_subject("docs: обновить CLAUDE.md") == "other"
    assert classify_subject("refactor: выделить ядро FSM") == "other"


@pytest.mark.unit
def test_legacy_english_subjects_without_conventional_prefix():
    """До перехода на conventional commits темы писались словами.

    Такие теги ещё встречаются в диапазонах между релизами, поэтому
    классификация не должна требовать двоеточия.
    """
    assert classify_subject("Fix TikTok download and crash reporting") == "fix"
    assert classify_subject("Add admin broadcast command") == "feat"
    assert classify_subject("Translate admin cookie panel messages") == "other"


@pytest.mark.unit
def test_russian_keywords_classify_without_prefix():
    assert classify_subject("Исправлена отправка больших файлов") == "fix"
    assert classify_subject("Добавлена поддержка Rutube") == "feat"
    assert classify_subject("Починка кэша file_id") == "fix"


@pytest.mark.unit
def test_prefix_wins_over_keyword_in_the_same_subject():
    """Тема с префиксом одной категории и словом другой не двоится.

    `fix: ... добавл...` — это исправление. Прежняя реализация на
    `git log --grep` относила такую тему сразу к двум секциям.
    """
    assert classify_subject("fix: добавлена валидация ссылок резолвера") == "fix"


@pytest.mark.unit
def test_commit_body_cannot_change_the_section():
    """Классификация смотрит только на тему, тело игнорируется.

    Регрессия на реальном диапазоне v1.2.2..HEAD: тело коммита
    `docs: зафиксировать замену ADR-001` содержит «добавлен», из-за чего
    коммит попадал в раздел «Новое», а `feat: быстрый путь TikTok» — ещё и
    в «Исправления».
    """
    assert classify_subject("docs: зафиксировать замену ADR-001") == "other"


@pytest.mark.unit
def test_each_commit_appears_exactly_once_in_notes():
    subjects = [
        "feat: быстрый путь TikTok без yt-dlp и перекодирования",
        "fix: валидировать ссылки резолвера TikTok перед скачиванием",
        "docs: зафиксировать замену ADR-001 быстрым путём",
        "test: не выдавать контракт ключей кэша за покрытие чтением",
    ]

    notes = render_notes(subjects, version="1.3.0", repository="mazixs/Nuvio")

    for subject in subjects:
        assert notes.count(f"- {subject}") == 1, subject


@pytest.mark.unit
def test_sections_group_commits_under_the_right_heading():
    notes = render_notes(
        [
            "feat: добавить доставку аудио из кэша file_id",
            "fix: очищать временные медиа при запуске",
            "ci: укрепить GitHub Actions",
        ],
        version="1.3.0",
        repository="mazixs/Nuvio",
    )

    feat_block = notes.split("### 🐛 Исправления")[0]
    fix_block = notes.split("### 🐛 Исправления")[1].split("### 📦")[0]
    other_block = notes.split("### 📦 Остальные изменения")[1]

    assert "- feat: добавить доставку аудио из кэша file_id" in feat_block
    assert "- fix: очищать временные медиа при запуске" in fix_block
    assert "- ci: укрепить GitHub Actions" in other_block


@pytest.mark.unit
def test_empty_sections_are_omitted():
    notes = render_notes(
        ["docs: описать локальный Bot API"],
        version="1.3.0",
        repository="mazixs/Nuvio",
    )

    assert "### ✨ Новое" not in notes
    assert "### 🐛 Исправления" not in notes
    assert "### 📦 Остальные изменения" in notes


@pytest.mark.unit
def test_notes_carry_version_and_install_snippet():
    """Инструкция установки обязана указывать именно выпускаемый тег.

    `compose.yaml` читает образ как `${TAG:-latest}`, поэтому без явного
    TAG пользователь получит latest вместо выпущенной версии.
    """
    notes = render_notes([], version="1.3.0", repository="mazixs/Nuvio")

    assert notes.startswith("## 🚀 Nuvio v1.3.0")
    assert "git clone --branch v1.3.0 --depth 1 https://github.com/mazixs/Nuvio.git" in notes
    assert "TAG=1.3.0 docker compose --env-file .secrets/.env up -d" in notes
    assert "cp .env.example .secrets/.env" in notes
