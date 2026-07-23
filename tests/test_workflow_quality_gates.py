"""Регрессионные тесты качества CI/CD."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _job_body(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
        workflow,
    )
    assert match, f"Задание {job_name!r} не найдено"
    return match.group("body")


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


def test_release_is_published_only_after_image():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    docker_job = _job_body(workflow, "docker")
    release_job = _job_body(workflow, "release")

    assert "needs: [test]" in docker_job
    assert "needs: [docker]" in release_job


def test_release_validates_semver_tag():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    test_job = _job_body(workflow, "test")

    assert "VALID_TAG_REGEX" in workflow
    assert "GITHUB_REF_NAME" in test_job
