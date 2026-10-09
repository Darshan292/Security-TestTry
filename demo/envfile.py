"""Minimal `.env` loader for local demo tooling (no dependency). KEY=value lines; `#` comments; optional quotes.

Values already set in the environment win (a Codespaces secret or `export` overrides the file). The file is
git-ignored; never commit it. Values are never printed.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env(path: Path | None = None) -> list[str]:
    """Load KEY=value pairs into os.environ (without overriding). Returns the names loaded, never the values."""
    p = Path(path) if path else ROOT / ".env"
    if not p.is_file():
        return []
    loaded = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if name not in os.environ and val:
            os.environ[name] = val
            loaded.append(name)
    return loaded


def permission_warning(path: Path | None = None) -> str | None:
    p = Path(path) if path else ROOT / ".env"
    try:
        if p.is_file() and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            return f"{p.name} is readable by other users; run: chmod 600 {p.name}"
    except OSError:
        pass
    return None
