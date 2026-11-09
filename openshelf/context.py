"""Shared services, plus covers and importing of the reader's own files."""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtPdf import QPdfDocument
from shiboken6 import isValid

from . import catalog, paths
from .bookfile import BookError, EpubBook
from .db import Library
from .net import Downloads, Net, Search, Streams
from .theme import typographic_cover


class Covers(QObject):
    """Cover art. Falls back to a designed cover when the Archive has nothing good.

    Covers on screen are fetched first. The rest trickle in behind them, ready for scrolling."""

    LIMIT = 8
    BACKGROUND = 2
    REMEMBERED = 500

    def __init__(self, net: Net, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self.memory: dict[str, QPixmap | None] = {}
        self.visible: OrderedDict[str, tuple[dict, list]] = OrderedDict()
        self.queue: OrderedDict[str, tuple[dict, list]] = OrderedDict()
        self.running: dict[str, list] = {}

    @staticmethod
    def _wants_designed_cover(book: dict) -> bool:
        # Gutenberg items only have a small black and white title page thumbnail.
        return book.get("source") == "ia" and book["id"].endswith("gut")

    def fallback(self, book: dict, width: int, height: int) -> QPixmap:
        return typographic_cover(book.get("title", ""), book.get("author", ""), width, height)

    def request(self, book: dict, callback: Callable[[QPixmap | None], None], owner: QObject) -> None:
        """Calls back with art, or None meaning 'use the designed cover'."""
        local = book.get("cover")
        key = local or book["id"]
        if key in self.memory:
            callback(self.memory[key])
            return
        if local and Path(local).exists():
            pixmap = QPixmap(local)
            self.memory[key] = pixmap if not pixmap.isNull() else None
            callback(self.memory[key])
            return
        if book.get("source") != "ia" or self._wants_designed_cover(book):
            callback(None)
            return

        if key in self.running:
            self.running[key].append((callback, owner))
            return
        waiting = self.visible if key in self.visible else self.queue
        waiting.setdefault(key, (book, []))[1].append((callback, owner))
        self._pump()

    def bump(self, book: dict) -> None:
        """Called when a cover first appears on screen."""
        key = book.get("cover") or book["id"]
        if key in self.queue:
            self.visible[key] = self.queue.pop(key)
        if key in self.visible:
            self.visible.move_to_end(key, last=False)
            self._pump()

    def _pump(self) -> None:
        while len(self.running) < self.LIMIT:
            if self.visible:
                source = self.visible
            elif self.queue and len(self.running) < self.BACKGROUND:
                source = self.queue
            else:
                return
            key, (book, waiters) = source.popitem(last=False)
            waiters = [w for w in waiters if isValid(w[1])]
            if waiters:  # Nobody is left waiting if the reader has moved on.
                self.running[key] = waiters
                self.net.get(
                    catalog.cover_url(book["id"]),
                    lambda data, _error, k=key: self._received(k, data), self, prefer_cache=True, retries=0)

    def _received(self, key: str, data: bytes | None) -> None:
        pixmap = QPixmap()
        # The Archive answers unknown items with a small generic PNG.
        is_placeholder = bool(data) and data[:4] == b"\x89PNG" and len(data) < 4000
        art = pixmap if data and not is_placeholder and pixmap.loadFromData(data) else None
        if art is not None:
            self.memory[key] = art
            while len(self.memory) > self.REMEMBERED:  # Paging through a catalog this size adds up.
                del self.memory[next(iter(self.memory))]
        for callback, owner in self.running.pop(key, []):
            if isValid(owner):
                callback(art)
        self._pump()


class CatalogCounts:
    """How many titles each part of the catalog holds.

    These ship with the app (see tools/update_counts.py), so they appear instantly and no
    one's copy has to ask the Archive for them."""

    def __init__(self) -> None:
        try:
            self.known: dict[str, int] = json.loads(
                (Path(__file__).parent / "assets" / "catalog_counts.json").read_text("utf-8"))
        except (OSError, ValueError):
            self.known = {}

    def get(self, query: str) -> int | None:
        return self.known.get(catalog.count_key(query))


class AppContext(QObject):
    """Everything the screens share. Navigation signals are handled by the main window."""

    open_book = Signal(dict)            # Read or listen now.
    show_detail = Signal(dict)
    show_search = Signal(str, str, str)  # Title, raw query, sort.
    search_text = Signal(str)
    show_shelf = Signal(str, object)
    toast = Signal(str)
    browse_requested = Signal()
    content_filter_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = Net(self)
        self.search = Search(self.net, self)
        self.streams = Streams(self.net, self)
        self.covers = Covers(self.net, self)
        self.library = Library(paths.database_path(), self)
        self.downloads = Downloads(self.net, self.library, self)
        self.counts = CatalogCounts()
        self.player = None  # Set by the main window.

    def ensure_in_library(self, book: dict, plan: dict | None = None) -> dict:
        record = dict(book)
        if plan:
            record["plan"] = plan
        return self.library.add_book(record)

    def with_plan(self, book: dict, ready: Callable[[dict], None]) -> None:
        """Call back with the stored book once what can be done with it is known."""
        stored = self.library.book(book["id"])
        if stored and stored.get("plan"):
            ready(stored)
            return

        def received(payload: object | None, error: str) -> None:
            if isinstance(payload, dict) and payload.get("metadata"):
                ready(self.ensure_in_library(book, catalog.analyze_item(payload)))
            else:
                self.toast.emit(error or "Couldn't load this book.")

        self.net.get_json(catalog.metadata_url(book["id"]), received, self)

    def read_now(self, book: dict) -> None:
        """Open a book directly from its cover. Reading online never adds it to the library."""
        def ready(stored: dict) -> None:
            if stored["plan"].get("available"):
                self.open_book.emit(stored)
            else:
                self.toast.emit("This title is only available to borrow, so it can't be opened here.")

        self.with_plan(book, ready)

    def start_download(self, book: dict) -> None:
        def ready(stored: dict) -> None:
            if not Downloads.files_for(stored["plan"]):
                self.toast.emit("This book can't be downloaded.")
                return
            self.downloads.start(stored, stored["plan"])
            self.toast.emit(f"Downloading “{stored['title']}”")

        self.with_plan(book, ready)

    def toggle_want(self, book: dict) -> None:
        stored = self.ensure_in_library(book)
        wanted = not stored["want"]
        self.library.set_want(book["id"], wanted)
        self.toast.emit("Added to Want to Read" if wanted else "Removed from Want to Read")

    def import_file(self, source: str) -> dict:
        """Copy an EPUB or PDF into the library. Raises BookError if it can't be read."""
        path = Path(source)
        suffix = path.suffix.lower()
        if suffix not in (".epub", ".pdf"):
            raise BookError(f"{path.name} isn't an EPUB or PDF.")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        book_id = f"local:{digest.hexdigest()[:20]}"
        existing = self.library.book(book_id)
        if existing:
            return existing
        target = paths.downloads_dir() / f"{book_id.replace(':', '-')}{suffix}"
        shutil.copyfile(path, target)
        cover_path = paths.covers_dir() / f"{book_id.replace(':', '-')}.png"
        try:
            if suffix == ".epub":
                title, author, plan, local = self._inspect_epub(target, path, cover_path)
            else:
                title, author, plan, local = self._inspect_pdf(target, path, cover_path)
        except BookError:
            target.unlink(missing_ok=True)
            raise
        record = {
            "id": book_id, "title": title, "author": author, "kind": "text", "source": "local",
            "cover": str(cover_path) if cover_path.exists() else None, "plan": plan,
        }
        self.library.add_book(record)
        self.library.set_local(book_id, local)
        return self.library.book(book_id)  # type: ignore[return-value]

    @staticmethod
    def _inspect_epub(target: Path, source: Path, cover_path: Path):
        book = EpubBook(target)
        art = book.cover_image()
        title, author = book.title or source.stem, book.author or "Unknown author"
        book.close()
        if art:
            image = QImage.fromData(art)
            if not image.isNull():
                image.scaledToWidth(500, Qt.TransformationMode.SmoothTransformation).save(str(cover_path))
        plan = {"kind": "text", "views": ["text"], "default_view": "text", "available": True,
                "epub": {"name": target.name, "size": target.stat().st_size, "original": True}}
        return title, author, plan, {"epub": str(target)}

    @staticmethod
    def _inspect_pdf(target: Path, source: Path, cover_path: Path):
        document = QPdfDocument()
        document.load(str(target))
        if document.status() != QPdfDocument.Status.Ready or document.pageCount() == 0:
            raise BookError(f"{source.name} couldn't be opened. It may be damaged or password protected.")
        title = (document.metaData(QPdfDocument.MetaDataField.Title) or "").strip() or source.stem
        author = (document.metaData(QPdfDocument.MetaDataField.Author) or "").strip() or "Unknown author"
        size = document.pagePointSize(0)
        ratio = size.height() / size.width() if size.width() else 1.4
        document.render(0, QSize(500, int(500 * ratio))).save(str(cover_path))
        plan = {"kind": "text", "views": ["scan"], "default_view": "scan", "available": True,
                "pdf": {"name": target.name, "size": target.stat().st_size}}
        document.close()
        return title, author, plan, {"pdf": str(target)}
