"""Выводит последний точный nightly-выпуск yt-dlp из каталога PyPI."""

from __future__ import annotations

import json
import re
import sys
from urllib.request import urlopen


def main() -> None:
    with urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=15) as response:
        releases = json.load(response)["releases"]
    candidates = []
    for version, files in releases.items():
        match = re.fullmatch(r"(\d{4})\.(\d+)\.(\d+)\.(\d+)\.dev0", version)
        if match and files and not all(item.get("yanked") for item in files):
            candidates.append((tuple(map(int, match.groups())), version))
    if not candidates:
        raise RuntimeError("nightly-версии yt-dlp не найдены")
    sys.stdout.write(max(candidates)[1] + "\n")


if __name__ == "__main__":
    main()
