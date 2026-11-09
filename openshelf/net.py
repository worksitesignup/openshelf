"""Networking on Qt's event loop, so the interface never blocks."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest,
)
from shiboken6 import isValid

from . import USER_AGENT, catalog, paths

Callback = Callable[[bytes | None, str], None]


def _friendly(reply: QNetworkReply) -> str:
    code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if code in (401, 403):
        return "The Internet Archive doesn't allow this file to be downloaded."
    if code == 404:
        return "The Internet Archive couldn't find this file."
    if code and int(code) >= 500:
        return "The Internet Archive is having trouble right now. Try again in a moment."
    if reply.error() in (
        QNetworkReply.NetworkError.HostNotFoundError,
        QNetworkReply.NetworkError.TemporaryNetworkFailureError,
        QNetworkReply.NetworkError.NetworkSessionFailedError,
        QNetworkReply.NetworkError.UnknownNetworkError,
    ):
        return "You appear to be offline."
    if reply.error() == QNetworkReply.NetworkError.TimeoutError:
        return "The connection timed out."
    return reply.errorString()


class Net(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        cache.setCacheDirectory(str(paths.web_cache_dir()))
        cache.setMaximumCacheSize(800 * 1024 * 1024)
        self.nam.setCache(cache)
        self._live: set[QNetworkReply] = set()

    def _request(self, url: str, timeout: int, prefer_cache: bool, store: bool = True) -> QNetworkRequest:
        request = QNetworkRequest(QUrl.fromEncoded(url.encode()))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        request.setTransferTimeout(timeout)
        if not store:
            request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        if prefer_cache:
            request.setAttribute(
                QNetworkRequest.Attribute.CacheLoadControlAttribute,
                QNetworkRequest.CacheLoadControl.PreferCache,
            )
        return request

    def get(
        self, url: str, callback: Callback, owner: QObject | None = None,
        prefer_cache: bool = False, retries: int = 1, timeout: int = 30_000, store: bool = True,
    ) -> None:
        """Fetch a URL. The callback is skipped if owner has been destroyed meanwhile."""
        reply = self.nam.get(self._request(url, timeout, prefer_cache, store))
        self._live.add(reply)

        def finished() -> None:
            self._live.discard(reply)
            reply.deleteLater()
            if owner is not None and not isValid(owner):
                return
            if reply.error() == QNetworkReply.NetworkError.NoError:
                callback(bytes(reply.readAll().data()), "")
                return
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
            if retries > 0 and (not code or int(code) >= 500):
                QTimer.singleShot(
                    900, self, lambda: self.get(url, callback, owner, prefer_cache, retries - 1, timeout, store))
                return
            callback(None, _friendly(reply))

        reply.finished.connect(finished)

    def get_json(self, url: str, callback: Callable[[object | None, str], None],
                 owner: QObject | None = None, **options) -> None:
        def parsed(data: bytes | None, error: str) -> None:
            if data is None:
                callback(None, error)
                return
            try:
                callback(json.loads(data), "")
            except ValueError:
                callback(None, "The Internet Archive sent an unreadable response.")

        self.get(url, parsed, owner, **options)

    def download(self, url: str, destination: Path) -> "FileDownload":
        return FileDownload(self, url, destination)


class FileDownload(QObject):
    """One file, written to a .part file and only renamed into place when complete."""

    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, net: Net, url: str, destination: Path) -> None:
        super().__init__(net)
        self.destination = destination
        self.part = destination.with_name(destination.name + ".part")
        self.file = QFile(str(self.part))
        self.cancelled = False
        if not self.file.open(QIODevice.OpenModeFlag.WriteOnly):
            QTimer.singleShot(0, self, lambda: self.failed.emit("Couldn't write to disk."))
            return
        request = net._request(url, 60_000, False)
        request.setAttribute(
            QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        self.reply = net.nam.get(request)
        self.reply.readyRead.connect(self._write)
        self.reply.downloadProgress.connect(lambda got, total: self.progress.emit(got, total))
        self.reply.finished.connect(self._finished)

    def _write(self) -> None:
        self.file.write(self.reply.readAll())

    def cancel(self) -> None:
        self.cancelled = True
        if hasattr(self, "reply"):
            self.reply.abort()

    def _finished(self) -> None:
        self._write()
        self.file.close()
        error = self.reply.error()
        message = _friendly(self.reply) if error != QNetworkReply.NetworkError.NoError else ""
        self.reply.deleteLater()
        if error != QNetworkReply.NetworkError.NoError:
            self.part.unlink(missing_ok=True)
            if not self.cancelled:
                self.failed.emit(message)
            self.deleteLater()
            return
        os.replace(self.part, self.destination)
        self.done.emit(str(self.destination))
        self.deleteLater()


class Search(QObject):
    """Catalog queries, cached briefly so moving around the app is instant."""

    TTL = 30 * 60

    def __init__(self, net: Net, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self._cache: dict[str, tuple[float, list[dict], int]] = {}
        self._fetching: dict[str, list[tuple[Callable, QObject | None]]] = {}

    def run(self, query: str, callback: Callable[[list[dict], int, str], None],
            owner: QObject | None = None, sort: str = "downloads desc", rows: int = 40,
            page: int = 1) -> None:
        url = catalog.search_url(query, sort, rows, page)
        cached = self._cache.get(url)
        if cached and time.time() - cached[0] < self.TTL:
            QTimer.singleShot(0, owner or self, lambda: callback(cached[1], cached[2], ""))
            return

        if url in self._fetching:  # Already on its way, perhaps fetched ahead of this click.
            self._fetching[url].append((callback, owner))
            return
        self._fetching[url] = [(callback, owner)]

        def received(payload: object | None, error: str) -> None:
            if isinstance(payload, dict):
                books, total = catalog.parse_search(payload)
                books = catalog.dedupe_editions(books)
                self._cache[url] = (time.time(), books, total)
                result = (books, total, "")
            else:
                result = ([], 0, error or "Search failed.")
            for waiter, waiter_owner in self._fetching.pop(url):
                if waiter_owner is None or isValid(waiter_owner):
                    waiter(*result)

        self.net.get_json(url, received, self)


class Streams(QObject):
    """Fetch a whole book into the temporary cache so it can be read without keeping it."""

    def __init__(self, net: Net, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net

    def path_for(self, identifier: str, name: str) -> Path:
        safe = f"{identifier}__{name}".replace("/", "_").replace("\\", "_")
        return paths.stream_dir() / safe

    def fetch(self, identifier: str, name: str) -> FileDownload | Path:
        """Return the cached path, or a running download for it."""
        target = self.path_for(identifier, name)
        if target.exists() and target.stat().st_size:
            os.utime(target)
            return target
        return self.net.download(catalog.file_url(identifier, name), target)


class Downloads(QObject):
    """Books the reader explicitly chose to keep offline."""

    progress = Signal(str, float)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self, net: Net, library, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self.library = library
        self.jobs: dict[str, dict] = {}

    def is_active(self, book_id: str) -> bool:
        return book_id in self.jobs

    @staticmethod
    def files_for(plan: dict) -> list[tuple[str, str, int]]:
        """(role, file name, size) for everything needed to read a book offline."""
        out: list[tuple[str, str, int]] = []
        if plan.get("kind") == "audio":
            for track in plan.get("tracks", []):
                out.append(("track", track["name"], track.get("size", 0)))
            return out
        if plan.get("default_view") == "scan":
            if plan.get("pdf"):
                out.append(("pdf", plan["pdf"]["name"], plan["pdf"].get("size", 0)))
        elif plan.get("epub"):
            out.append(("epub", plan["epub"]["name"], plan["epub"].get("size", 0)))
        elif plan.get("text"):
            out.append(("text", plan["text"]["name"], plan["text"].get("size", 0)))
        return out

    def start(self, book: dict, plan: dict) -> None:
        files = self.files_for(plan)
        if not files or book["id"] in self.jobs:
            return
        folder = paths.downloads_dir() / book["id"].replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        self.jobs[book["id"]] = {
            "folder": folder, "files": files, "index": 0, "local": {},
            "done_bytes": 0, "total": sum(f[2] for f in files) or 1, "current": None,
        }
        self._next(book["id"])

    def _next(self, book_id: str) -> None:
        job = self.jobs[book_id]
        if job["index"] >= len(job["files"]):
            del self.jobs[book_id]
            self.library.set_local(book_id, job["local"])
            self.finished.emit(book_id)
            return
        role, name, size = job["files"][job["index"]]
        target = job["folder"] / Path(name).name
        download = self.net.download(catalog.file_url(book_id, name), target)
        job["current"] = download

        def advanced(got: int, _total: int) -> None:
            if book_id in self.jobs:
                self.progress.emit(book_id, min(1.0, (job["done_bytes"] + got) / job["total"]))

        def completed(path: str) -> None:
            if role == "track":
                job["local"].setdefault("tracks", {})[name] = path
            else:
                job["local"][role] = path
            job["done_bytes"] += size
            job["index"] += 1
            self._next(book_id)

        def errored(message: str) -> None:
            self.jobs.pop(book_id, None)
            self.failed.emit(book_id, message)

        download.progress.connect(advanced)
        download.done.connect(completed)
        download.failed.connect(errored)

    def cancel(self, book_id: str) -> None:
        job = self.jobs.pop(book_id, None)
        if job and job["current"]:
            job["current"].cancel()
        self.failed.emit(book_id, "")

    def remove(self, book: dict) -> None:
        """Delete a book's offline files, leaving progress and notes alone."""
        for path in self._local_paths(book):
            Path(path).unlink(missing_ok=True)
        folder = paths.downloads_dir() / book["id"].replace("/", "_")
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        self.library.set_local(book["id"], {})

    @staticmethod
    def _local_paths(book: dict) -> list[str]:
        out: list[str] = []
        for key, value in (book.get("local") or {}).items():
            if key == "tracks":
                out.extend(value.values())
            elif isinstance(value, str):
                out.append(value)
        return out
