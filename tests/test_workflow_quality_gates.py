"""Регрессионные тесты качества CI/CD."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_ci_runs_full_suite_with_coverage():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert '-m "syntax or unit"' not in workflow
    assert "coverage run --branch -m pytest tests/" in workflow
    assert "coverage report --fail-under=40" in workflow


def test_release_runs_full_suite_with_coverage_and_ruff():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert '-m "syntax or unit"' not in workflow
    assert "ruff check --output-format=github ." in workflow
    assert "coverage run --branch -m pytest tests/" in workflow
    assert "coverage report --fail-under=40" in workflow
