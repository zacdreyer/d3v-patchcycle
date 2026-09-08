"""Check local Markdown file targets without requesting external sites."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
    failures = []
    for path in files:
        for target in re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            relative = unquote(parsed.path)
            destination = (
                root / relative.lstrip("/") if relative.startswith("/") else path.parent / relative
            )
            if not destination.exists():
                failures.append(f"{path.relative_to(root)}: missing {target}")
    for failure in failures:
        print(failure)
    if not failures:
        print(f"Documentation links: {len(files)} files checked")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
