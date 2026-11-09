"""The sandboxed web view that displays reflowable text."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QBuffer, QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
    QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestJob, QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from .bookfile import BaseBook, BookError

SCHEME = b"osbook"
ASSETS = Path(__file__).parent / "assets"
_ASSET_TYPES = {"reader.html": b"text/html", "reader.css": b"text/css", "reader.js": b"text/javascript"}


def register_scheme() -> None:
    """Must run before the QApplication is created."""
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
    QWebEngineUrlScheme.registerScheme(scheme)


class _SchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves the reader's own files and images from inside open books."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.books: dict[str, BaseBook] = {}

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 (Qt override)
        path = job.requestUrl().path(QUrl.ComponentFormattingOption.FullyDecoded).lstrip("/")
        owner, _, rest = path.partition("/")
        data: bytes | None = None
        mime = b""
        if owner == "app":
            if rest in _ASSET_TYPES:
                data, mime = (ASSETS / rest).read_bytes(), _ASSET_TYPES[rest]
            elif rest == "qwebchannel.js":
                source = QFile(":/qtwebchannel/qwebchannel.js")
                if source.open(QIODevice.OpenModeFlag.ReadOnly):
                    data, mime = bytes(source.readAll().data()), b"text/javascript"
        elif owner in self.books:
            try:
                found = self.books[owner].resource(rest)
            except (BookError, KeyError, OSError):
                found = None
            if found:
                data, mime = found[0], found[1].encode()
        if data is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime, buffer)


class _NetworkBlocker(QWebEngineUrlRequestInterceptor):
    """Book pages get no network at all, even if something slips past the sanitizer."""

    def interceptRequest(self, info) -> None:  # noqa: N802 (Qt override)
        if info.requestUrl().scheme() not in ("osbook", "data", "qrc"):
            info.block(True)


class ReaderEngine(QObject):
    """One shared private browser profile for all reader windows."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)  # No name means nothing is kept on disk.
        self.handler = _SchemeHandler(self)
        self.blocker = _NetworkBlocker(self)
        self.profile.installUrlSchemeHandler(SCHEME, self.handler)
        self.profile.setUrlRequestInterceptor(self.blocker)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        settings = self.profile.settings()
        attribute = QWebEngineSettings.WebAttribute
        settings.setAttribute(attribute.JavascriptEnabled, True)
        settings.setAttribute(attribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(attribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(attribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(attribute.PluginsEnabled, False)
        settings.setAttribute(attribute.PdfViewerEnabled, False)
        settings.setAttribute(attribute.NavigateOnDropEnabled, False)


class _Bridge(QObject):
    message = Signal(object)

    @Slot(str)
    def send(self, text: str) -> None:
        try:
            self.message.emit(json.loads(text))
        except ValueError:
            pass


class _Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame) -> bool:  # noqa: N802 (Qt override)
        return url.scheme() == "osbook" and url.path().endswith("/app/reader.html")

    def javaScriptConsoleMessage(self, level, message, line, source) -> None:  # noqa: N802 (Qt override)
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"[reader] {message} ({source}:{line})")


class TextView(QWebEngineView):
    message = Signal(object)
    ready = Signal()

    def __init__(self, engine: ReaderEngine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.is_ready = False
        self.setContextMenuPolicy(self.contextMenuPolicy().NoContextMenu)
        self._page = _Page(engine.profile, self)
        self.bridge = _Bridge(self)
        self.channel = QWebChannel(self._page)
        self.channel.registerObject("bridge", self.bridge)
        self._page.setWebChannel(self.channel, QWebEngineScript.ScriptWorldId.MainWorld)
        self.setPage(self._page)
        self.bridge.message.connect(self._received)
        self.setAcceptDrops(False)
        self.load(QUrl("osbook:/app/reader.html"))

    def _received(self, message: dict) -> None:
        if message.get("type") == "ready":
            self.is_ready = True
            self.ready.emit()
        else:
            self.message.emit(message)

    def set_background(self, color: str) -> None:
        self._page.setBackgroundColor(QColor(color))

    def call(self, function: str, *args) -> None:
        arguments = ",".join(json.dumps(a) for a in args)
        self._page.runJavaScript(f"OS.{function}({arguments})")

    def shutdown(self) -> None:
        self.setPage(None)
        self._page.deleteLater()
