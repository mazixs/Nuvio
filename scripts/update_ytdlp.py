"""Обновляет точную версию yt-dlp и оба lock-файла одной командой."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^yt-dlp\[default\]==[^\s]+$", re.MULTILINE)
VERSION = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+\.dev0)?$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Точная версия, например 2026.9.16.232951.dev0")
    parser.add_argument("--skip-checks", action="store_true", help="Только обновить lock-файлы")
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        parser.error("нужна точная версия yt-dlp, без канала или URL")

    source = ROOT / "requirements.in"
    original = source.read_text(encoding="utf-8")
    replacement, count = PIN.subn(f"yt-dlp[default]=={args.version}", original)
    if count != 1:
        raise RuntimeError("в requirements.in ожидается ровно один pin yt-dlp[default]")

    files = [
        source,
        ROOT / "requirements.txt",
        ROOT / "requirements-dev.txt",
        ROOT / "AGENTS.md",
        ROOT / "docs/PRD.md",
        ROOT / "docs/technical/youtube-download-runbook.md",
    ]
    backup = {path: path.read_bytes() for path in files}
    try:
        source.write_text(replacement, encoding="utf-8")
        for inputs, output in (
            ("requirements.in", "requirements.txt"),
            ("requirements-dev.in", "requirements-dev.txt"),
        ):
            subprocess.run(
                ["uv", "pip", "compile", "--python-version", "3.14", "--generate-hashes",
                 "--output-file", output, inputs],
                cwd=ROOT,
                check=True,
            )
        for output in files[1:]:
            if output.suffix != ".txt":
                continue
            lock = output.read_text(encoding="utf-8")
            if f"yt-dlp=={args.version}" not in lock:
                raise RuntimeError(f"{output.name} не закрепил версию {args.version}")
        for path, pattern in (
            (ROOT / "AGENTS.md", r"(\*\*Скачивание\*\*: \[yt-dlp\]\([^)]*\) )\S+"),
            (ROOT / "docs/PRD.md", r"(`yt-dlp\[default\]` )\S+"),
            (ROOT / "docs/technical/youtube-download-runbook.md", r"(Текущая закрепленная версия - `)\S+(?=`)"),
        ):
            content = path.read_text(encoding="utf-8")
            updated, count = re.subn(pattern, lambda match: match.group(1) + args.version, content, count=1)
            if count != 1:
                raise RuntimeError(f"не найдено место версии в {path.name}")
            path.write_text(updated, encoding="utf-8")
        if not args.skip_checks:
            subprocess.run(
                ["uv", "pip", "sync", "--python", sys.executable, "requirements-dev.txt"],
                cwd=ROOT,
                check=True,
            )
            subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=ROOT, check=True)
            subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp=/tmp/nuvio-update-ytdlp"], cwd=ROOT, check=True)
    except BaseException:
        for path, contents in backup.items():
            path.write_bytes(contents)
        raise

    subprocess.run(["git", "diff", "--", *(str(path.relative_to(ROOT)) for path in files)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
