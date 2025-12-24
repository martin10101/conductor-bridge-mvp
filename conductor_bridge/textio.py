"""Small text I/O helpers (Windows-friendly)."""

from __future__ import annotations

from pathlib import Path


def read_text_auto(path: Path) -> str:
    data = path.read_bytes()
    # BOM sniffing for common Windows cases
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    return data.decode("utf-8", errors="replace")

