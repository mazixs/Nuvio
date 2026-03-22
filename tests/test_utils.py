#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Basic structure tests for the project utilities."""

from pathlib import Path

import pytest


@pytest.mark.unit
class TestPathUtils:
    """Checks that the expected top-level paths exist."""

    def test_project_root_exists(self, project_root: Path):
        assert project_root.exists(), "Project root directory is missing"
        assert project_root.is_dir(), "Project root path must be a directory"

    def test_utils_dir_exists(self, utils_dir: Path):
        assert utils_dir.exists(), "utils directory is missing"
        assert utils_dir.is_dir(), "utils path must be a directory"

    def test_main_py_exists(self, project_root: Path):
        main_file = project_root / "main.py"
        assert main_file.exists(), "main.py file is missing"
        assert main_file.is_file(), "main.py path must be a file"


@pytest.mark.unit
class TestConfigStructure:
    """Checks that the core configuration files exist."""

    def test_config_py_exists(self, project_root: Path):
        config_file = project_root / "config.py"
        assert config_file.exists(), "config.py file is missing"

    def test_messages_py_exists(self, project_root: Path):
        messages_file = project_root / "messages.py"
        assert messages_file.exists(), "messages.py file is missing"

    def test_requirements_exists(self, project_root: Path):
        requirements_file = project_root / "requirements.txt"
        assert requirements_file.exists(), "requirements.txt file is missing"


@pytest.mark.unit
class TestUtilsModules:
    """Checks that the critical utility modules are present."""

    def test_youtube_utils_exists(self, utils_dir: Path):
        module = utils_dir / "youtube_utils.py"
        assert module.exists(), "youtube_utils.py module is missing"

    def test_telegram_utils_exists(self, utils_dir: Path):
        module = utils_dir / "telegram_utils.py"
        assert module.exists(), "telegram_utils.py module is missing"

    def test_logger_exists(self, utils_dir: Path):
        module = utils_dir / "logger.py"
        assert module.exists(), "logger.py module is missing"
