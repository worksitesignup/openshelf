"""Where Open Shelf keeps its files. OPENSHELF_HOME overrides everything (used by tests)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def _base(kind: QStandardPaths.StandardLocation, sub: str) -> Path:
    override = os.environ.get("OPENSHELF_HOME")
    if override:
        path = Path(override) / sub
    else:
        path = Path(QStandardPaths.writableLocation(kind))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _base(QStandardPaths.StandardLocation.AppDataLocation, "data")


def cache_dir() -> Path:
    return _base(QStandardPaths.StandardLocation.CacheLocation, "cache")


def database_path() -> Path:
    return data_dir() / "library.sqlite3"


def downloads_dir() -> Path:
    """Books the user chose to keep, plus imported files."""
    path = data_dir() / "books"
    path.mkdir(parents=True, exist_ok=True)
    return path


def covers_dir() -> Path:
    path = data_dir() / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stream_dir() -> Path:
    """Temporary copies of books being read without downloading them."""
    path = cache_dir() / "streams"
    path.mkdir(parents=True, exist_ok=True)
    return path


def web_cache_dir() -> Path:
    path = cache_dir() / "web"
    path.mkdir(parents=True, exist_ok=True)
    return path


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def trim_stream_cache(limit_bytes: int = 1_500_000_000) -> None:
    """Delete the least recently used streamed books once over the limit."""
    entries = []
    for entry in stream_dir().iterdir():
        stat = entry.stat()
        entries.append((stat.st_mtime, dir_size(entry) if entry.is_dir() else stat.st_size, entry))
    entries.sort(key=lambda e: e[0])
    total = sum(e[1] for e in entries)
    for _mtime, size, entry in entries:
        if total <= limit_bytes:
            break
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        total -= size
