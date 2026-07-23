# GitHub Actions DevOps Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вернуть CI в зелёное состояние и сделать выпуск контейнера воспроизводимым, минимально привилегированным и проверяющим тот же артефакт, который получает GHCR.

**Architecture:** CI использует единый зафиксированный набор инструментов разработки, валидирует workflow через actionlint и собирает контейнеры Buildx с GHA-кэшем. Release сначала публикует образ только по digest, запускает smoke-проверку этого digest и лишь затем создаёт пользовательские теги и GitHub Release.

**Tech Stack:** GitHub Actions, Ruff 0.16.0, pytest/coverage, Docker Buildx, GHCR, actionlint 1.7.12.

## Global Constraints

- Работа выполняется в текущем `main` по явному указанию пользователя.
- Runtime-образ не содержит pytest, coverage и Ruff.
- Все внешние GitHub Actions закрепляются полным commit SHA.
- Права `GITHUB_TOKEN` выдаются отдельно каждому заданию по принципу минимальных привилегий.
- Релиз разрешён только для semver-тега, commit которого входит в `origin/main`.
- Smoke-проверка выполняется для того же digest, который затем получает теги GHCR.

---

### Task 1: Воспроизводимые инструменты разработки

**Files:**
- Create: `requirements-dev.txt`
- Modify: `requirements.txt`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Test: `tests/test_workflow_quality_gates.py`

**Interfaces:**
- Consumes: текущий набор runtime-зависимостей.
- Produces: единая команда `python -m pip install --requirement requirements-dev.txt` и фиксированная политика Ruff.

- [ ] **Step 1: Добавить падающие проверки**

Проверить, что Ruff закреплён как `ruff==0.16.0`, pytest/coverage находятся только в dev-файле, а workflow не выполняют `pip install ruff` без версии.

- [ ] **Step 2: Убедиться в корректном RED**

Run: `.venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Expected: FAIL на отсутствии `requirements-dev.txt` и незакреплённой установке Ruff.

- [ ] **Step 3: Разделить runtime/dev-зависимости и зафиксировать lint policy**

Создать `requirements-dev.txt`, перенести туда pytest/coverage и добавить Ruff 0.16.0. В `pyproject.toml` явно выбрать прежний набор правил `E4`, `E7`, `E9`, `F`.

- [ ] **Step 4: Проверить GREEN**

Run: `.venv/bin/python -m pip install --requirement requirements-dev.txt && .venv/bin/ruff check . && .venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Expected: PASS.

### Task 2: Укрепление CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_workflow_quality_gates.py`

**Interfaces:**
- Consumes: `requirements-dev.txt`.
- Produces: минимальные права, concurrency, таймауты, actionlint и Buildx-кэш.

- [ ] **Step 1: Добавить падающие контрактные проверки**

Проверить `permissions: contents: read`, concurrency, `timeout-minutes`, pull request для `develop`, SHA внешних Actions, actionlint по digest и GHA-кэш Buildx.

- [ ] **Step 2: Убедиться в корректном RED**

Run: `.venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Expected: FAIL на отсутствующих DevOps-гарантиях.

- [ ] **Step 3: Реализовать минимальный CI**

Закрепить Actions по SHA, добавить actionlint, таймауты, concurrency и заменить `docker build` на Buildx с `load: true`, `cache-from` и `cache-to`.

- [ ] **Step 4: Проверить GREEN и actionlint**

Run: `.venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Run: `docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667 -color .github/workflows/*.yml`

Expected: PASS без замечаний.

### Task 3: Проверяемый release pipeline

**Files:**
- Modify: `.github/workflows/release.yml`
- Test: `tests/test_workflow_quality_gates.py`

**Interfaces:**
- Consumes: semver tag и единый Nuvio Dockerfile.
- Produces: canonical image digest, проверенный smoke-тестом до создания тегов.

- [ ] **Step 1: Добавить падающие проверки**

Проверить job-level permissions, `production` environment, проверку достижимости тега из `origin/main`, build-by-digest, smoke по digest и создание тегов через `imagetools create`.

- [ ] **Step 2: Убедиться в корректном RED**

Run: `.venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Expected: FAIL на повторной сборке и глобальных write-правах.

- [ ] **Step 3: Перестроить выпуск**

Собрать и отправить canonical digest с provenance/SBOM, проверить этот digest, затем создать semver-теги. Выдать `packages: write` только docker job и `contents: write` только release job.

- [ ] **Step 4: Проверить GREEN**

Run: `.venv/bin/pytest tests/test_workflow_quality_gates.py -q`

Run: actionlint для обоих workflow.

Expected: PASS.

### Task 4: Воспроизводимые Docker-основания и документация

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.telegram-bot-api`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/PRD.md`
- Modify: `docs/development/contributing.md`
- Test: `tests/test_environment_template.py`
- Test: `tests/test_workflow_quality_gates.py`

**Interfaces:**
- Consumes: проверенные digest текущих базовых образов.
- Produces: воспроизводимые Docker FROM и актуальные команды разработки.

- [ ] **Step 1: Добавить падающие проверки digest**

Проверить, что оба базовых образа используют `@sha256:`.

- [ ] **Step 2: Закрепить основания и обновить инструкции**

Закрепить Python и Debian образы по digest, заменить установку `requirements.txt` для разработки на `requirements-dev.txt`, описать release-by-digest.

- [ ] **Step 3: Выполнить полную проверку**

Run: `.venv/bin/ruff check .`

Run: `.venv/bin/python -m coverage erase && .venv/bin/python -m coverage run --branch -m pytest tests/ && .venv/bin/python -m coverage report --fail-under=40`

Run: actionlint, Compose config и сборка обоих Docker-образов.

Expected: все команды завершаются с кодом 0.

### Task 5: Настройки GitHub

**Files:**
- External: repository Actions permissions
- External: `main` branch protection/ruleset
- External: `production` environment

**Interfaces:**
- Consumes: успешно опубликованный workflow с существующими check names.
- Produces: обязательные CI checks, запрет опасных прямых изменений и ограничение Actions.

- [ ] **Step 1: После push дождаться зелёного CI**

Run: `gh run watch <run-id> --repo mazixs/Nuvio --exit-status`

Expected: success.

- [ ] **Step 2: Включить SHA pinning и ограничить разрешённые Actions**

Разрешить GitHub-owned Actions, `docker/*` и `softprops/action-gh-release`, требуя полные SHA.

- [ ] **Step 3: Защитить main**

Потребовать PR и успешные проверки lint, tests и Docker; запретить force-push и удаление.

- [ ] **Step 4: Настроить production environment**

Создать окружение и ограничить релиз защищёнными тегами после проверки доступных правил API.
