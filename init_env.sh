#!/usr/bin/env bash
# Headless bootstrap for the systemd service.

set -euo pipefail

SCRIPT_DIR="$(
  CDPATH= cd -- "$(dirname -- "$0")" && pwd
)"
cd "$SCRIPT_DIR"

umask 077
mkdir -p .secrets downloads logs temp

migrate_legacy_file() {
  local source_path="$1"
  local target_path="$2"

  if [ ! -e "$source_path" ]; then
    return 0
  fi

  if [ -e "$target_path" ]; then
    echo "=== Keeping legacy file $source_path because $target_path already exists ==="
    return 0
  fi

  mkdir -p "$(dirname -- "$target_path")"
  mv "$source_path" "$target_path"
  echo "=== Migrated $source_path -> $target_path ==="
}

best_effort_git_update() {
  local current_branch=""
  local remote_target=""

  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "=== Not a git repo, skipping git update ==="
    return 0
  fi

  echo "=== Git update ==="
  if ! git fetch --prune origin; then
    echo "=== git fetch failed, continuing with local checkout ==="
    return 0
  fi

  current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [ -n "$current_branch" ] && git show-ref --verify --quiet "refs/remotes/origin/$current_branch"; then
    remote_target="origin/$current_branch"
  elif git show-ref --verify --quiet refs/remotes/origin/main; then
    remote_target="origin/main"
  elif git show-ref --verify --quiet refs/remotes/origin/master; then
    remote_target="origin/master"
  fi

  if [ -z "$remote_target" ]; then
    echo "=== No matching remote branch found, keeping current checkout ==="
    return 0
  fi

  if git reset --hard "$remote_target"; then
    echo "=== Updated to $remote_target ==="
    return 0
  fi

  echo "=== git reset failed, continuing with local checkout ==="
  return 0
}

select_python_bin() {
  if [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
    printf '%s\n' "$SCRIPT_DIR/venv/bin/python"
    return 0
  fi

  if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    printf '%s\n' "$SCRIPT_DIR/.venv/bin/python"
    return 0
  fi

  echo "ERROR: virtual environment not found (expected venv/bin/python or .venv/bin/python)" >&2
  return 1
}

migrate_legacy_file ".env" ".secrets/.env"
migrate_legacy_file "www.youtube.com_cookies.txt" ".secrets/www.youtube.com_cookies.txt"
migrate_legacy_file "www.instagram.com_cookies.txt" ".secrets/www.instagram.com_cookies.txt"
migrate_legacy_file "www.tiktok.com_cookies.txt" ".secrets/www.tiktok.com_cookies.txt"

best_effort_git_update

PYTHON_BIN="$(select_python_bin)"

echo "=== Installing dependencies ==="
"$PYTHON_BIN" -m pip install --disable-pip-version-check --upgrade -r requirements.txt -q

echo "=== Validating runtime imports ==="
"$PYTHON_BIN" -c "import telegram, yt_dlp"

echo "=== Init complete ==="
