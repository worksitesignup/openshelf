"""Page-image reading: scans streamed from the Archive, or PDFs on disk."""
from __future__ import annotations

import os
import re
from itertools import chain
from typing import Callable

from PySide6.QtCore import QEasingCurve, QObject, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from . import catalog, paths
from .bookfile import BookError
from .net import Net

PageReady = Callable[[int, QPixmap | None], None]


def stream_width() -> int:
    """One image size per screen, so resizing the window never fetches pages again."""
    screen = QGuiApplication.primaryScreen()
    tallest = screen.size().height() * screen.devicePixelRatio() if screen else 1080
    wanted = tallest / 1.4
    return next((width for width in (800, 1200) if wanted <= width), 1800)


class RemotePages(QObject):
    """Scanned pages, fetched ahead of the reader until the whole book is on disk.

    Pages are taken outward from wherever the reader is, so the next pages are always
    first in line. They go in the temporary cache, not the library."""

    progress = Signal(int, int)
    CONCURRENT = 4
    SHARP = 1800
    MAX_ATTEMPTS = 3

    def __init__(self, net: Net, identifier: str, total: int, width: int,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self.identifier = identifier
        self.total = total
        self.width = width
        safe = re.sub(r"[^\w.-]", "_", identifier)
        self.folder = paths.stream_dir() / f"{safe}__pages_{width}"
        self.folder.mkdir(parents=True, exist_ok=True)
        os.utime(self.folder)
        self.have = {int(p.stem) for p in self.folder.glob("*.jpg") if p.stem.isdigit()}
        self.active: set[int] = set()
        self.attempts: dict[int, int] = {}
        self.waiting: dict[int, list[PageReady]] = {}
        self.center = 0
        self.stopped = False
        self.paused = False

    def _path(self, index: int):
        return self.folder / f"{index}.jpg"

    def request(self, index: int, _width: int, callback: PageReady) -> None:
        if index in self.have:
            QTimer.singleShot(0, self, lambda: self._decode(index, callback))
            return
        self.waiting.setdefault(index, []).append(callback)
        if self.attempts.get(index, 0) < 90:  # Asking again gives a failed page a fresh chance.
            self.attempts.pop(index, None)
        self._pump()

    def _decode(self, index: int, callback: PageReady) -> None:
        pixmap = QPixmap(str(self._path(index)))
        if pixmap.isNull():
            self.have.discard(index)
            self._path(index).unlink(missing_ok=True)
            callback(index, None)
        else:
            callback(index, pixmap)

    def focus(self, index: int) -> None:
        self.center = index
        self._pump()

    def _next_needed(self) -> int | None:
        order = chain(range(self.center, self.total), range(self.center - 1, -1, -1))
        for index in order:
            if index not in self.have and index not in self.active \
                    and self.attempts.get(index, 0) < self.MAX_ATTEMPTS:
                return index
        return None

    def _pump(self) -> None:
        if self.stopped or self.paused:
            return
        while len(self.active) < self.CONCURRENT:
            index = self._next_needed()
            if index is None:
                return
            self.active.add(index)
            self.net.get(
                catalog.page_image_url(self.identifier, index, self.width),
                lambda data, error, i=index: self._received(i, data, error),
                self, retries=0, store=False)

    def _received(self, index: int, data: bytes | None, error: str) -> None:
        self.active.discard(index)
        if self.stopped:
            return
        if data and len(data) > 500:
            temporary = self._path(index).with_suffix(".part")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, self._path(index))
            except OSError:
                self.stopped = True  # The disk is full or gone; reading can carry on without this.
                return
            self.have.add(index)
            self.attempts.pop(index, None)
            self.progress.emit(len(self.have), self.total)
            for callback in self.waiting.pop(index, []):
                self._decode(index, callback)
            self._pump()
            return
        missing = "couldn't find" in error
        self.attempts[index] = 99 if missing else self.attempts.get(index, 0) + 1
        if self.attempts[index] >= self.MAX_ATTEMPTS:
            for callback in self.waiting.pop(index, []):
                callback(index, None)
        if missing:
            self._pump()
        else:  # Give the Archive, or the connection, a moment before trying more.
            self.paused = True
            QTimer.singleShot(4000, self, self._resume)

    def _resume(self) -> None:
        self.paused = False
        self._pump()

    def sharp(self, index: int, callback: PageReady) -> None:
        """A full-resolution copy of one page, for zooming in."""
        def received(data: bytes | None, _error: str) -> None:
            pixmap = QPixmap()
            if data and pixmap.loadFromData(data):
                callback(index, pixmap)

        self.net.get(catalog.page_image_url(self.identifier, index, self.SHARP), received, self, prefer_cache=True)

    def stop(self) -> None:
        self.stopped = True
        self.waiting.clear()


class PdfPages(QObject):
    def __init__(self, path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.document = QPdfDocument(self)
        self.document.load(path)
        if self.document.status() != QPdfDocument.Status.Ready or self.document.pageCount() == 0:
            raise BookError("This PDF couldn't be opened. It may be damaged or password protected.")
        self.total = self.document.pageCount()

    def request(self, index: int, width: int, callback: PageReady) -> None:
        def render() -> None:
            size = self.document.pagePointSize(index)
            ratio = size.height() / size.width() if size.width() else 1.4
            image: QImage = self.document.render(index, QSize(width, int(width * ratio)))
            callback(index, QPixmap.fromImage(image) if not image.isNull() else None)

        QTimer.singleShot(0, self, render)

    def page_text(self, index: int) -> str:
        return self.document.getAllText(index).text()

    def sharp(self, index: int, callback: PageReady) -> None:
        self.request(index, 2000, callback)

    def focus(self, index: int) -> None:
        pass

    def stop(self) -> None:
        pass


class _Canvas(QWidget):
    def __init__(self, view: "ScanView") -> None:
        super().__init__(view)
        self.view = view
        self.press = None
        self.dragged = False

    def paintEvent(self, event) -> None:
        self.view.paint_canvas(self)

    def mousePressEvent(self, event) -> None:
        bars = self.view.horizontalScrollBar(), self.view.verticalScrollBar()
        self.press = (event.globalPosition(), bars[0].value(), bars[1].value())
        self.dragged = False

    def mouseMoveEvent(self, event) -> None:
        if self.press is None or self.view.zoom <= 1.0:
            return
        start, x, y = self.press
        delta = event.globalPosition() - start
        if abs(delta.x()) + abs(delta.y()) > 4:
            self.dragged = True
        if self.dragged:
            self.view.horizontalScrollBar().setValue(x - int(delta.x()))
            self.view.verticalScrollBar().setValue(y - int(delta.y()))

    def mouseReleaseEvent(self, event) -> None:
        self.press = None
        if not self.dragged:
            self.view.canvas_clicked(event.position().x() / max(1, self.width()))


class ScanView(QScrollArea):
    page_changed = Signal(int, int)
    zoom_changed = Signal()
    PAGE_RATIO = 1.5
    AHEAD = 8
    BEHIND = 3
    CACHE = 20
    TURN_MS = 240

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.canvas = _Canvas(self)
        self.setWidget(self.canvas)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.provider = None
        self.index = 0
        self.zoom = 1.0
        self.animate = True
        self.render_width = 800
        self.pages: dict[int, QPixmap] = {}
        self.loading: set[int] = set()
        self.failed: set[int] = set()
        self.sharp: dict[int, QPixmap] = {}
        self.sharp_loading: set[int] = set()
        self.background = QColor("#FFFFFF")
        self.placeholder = QColor("#EEEEEA")
        self.ink = QColor("#777777")
        self.from_frame: QPixmap | None = None
        self.to_frame: QPixmap | None = None
        self.direction = 1
        self.slide = 0.0
        self.turn = QVariantAnimation(self)
        self.turn.setDuration(self.TURN_MS)
        self.turn.setStartValue(0.0)
        self.turn.setEndValue(1.0)
        self.turn.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.turn.valueChanged.connect(self._slid)
        self.turn.finished.connect(self._turn_done)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_colors(self, background: str, line: str, muted: str) -> None:
        self.background, self.placeholder, self.ink = QColor(background), QColor(line), QColor(muted)
        self.viewport().setStyleSheet(f"background:{background}")
        self.canvas.update()

    @property
    def total(self) -> int:
        return self.provider.total if self.provider else 0

    def set_provider(self, provider, index: int) -> None:
        self.provider = provider
        self.render_width = stream_width()
        self.pages.clear()
        self.sharp.clear()
        self.loading.clear()
        self.failed.clear()
        self.go(index)

    def _spread(self) -> bool:
        return self.viewport().width() >= 1000 and self.zoom == 1.0

    def _visible_at(self, index: int) -> list[int]:
        if not self._spread() or index == 0:
            return [index]
        left = index if index % 2 == 1 else index - 1
        return [i for i in (left, left + 1) if i < self.total]

    def _visible(self) -> list[int]:
        return self._visible_at(self.index)

    def go(self, index: int) -> None:
        self._finish_turn()
        if not self.total:
            return
        self.index = max(0, min(index, self.total - 1))
        if self._spread() and self.index > 0 and self.index % 2 == 0:
            self.index -= 1
        self.failed.difference_update(self._visible())
        self._sync()
        self.provider.focus(self.index)
        self.page_changed.emit(self.index, self.total)

    def next(self) -> None:
        last = max(self._visible())
        if last + 1 < self.total:
            self._turn_to(last + 1, 1)

    def previous(self) -> None:
        if self.index > 0:
            self._turn_to(self.index - (2 if self._spread() and self.index > 1 else 1), -1)

    def _turn_to(self, index: int, direction: int) -> None:
        self._finish_turn()
        target = max(0, min(index, self.total - 1))
        if self._spread() and target > 0 and target % 2 == 0:
            target -= 1
        ready = all(page in self.pages for page in self._visible_at(target))
        if not (self.animate and self.zoom == 1.0 and ready and self.isVisible()):
            self.go(target)  # Only slide to a page that can be shown complete.
            return
        self.from_frame = self._frame()
        self.go(target)
        self.to_frame = self._frame()
        self.direction = direction
        self.slide = 0.0
        self.turn.start()

    def _slid(self, value: float) -> None:
        self.slide = float(value)
        self.canvas.update()

    def _turn_done(self) -> None:
        self.from_frame = self.to_frame = None
        self.canvas.update()

    def _finish_turn(self) -> None:
        if self.turn.state() == QVariantAnimation.State.Running:
            self.turn.stop()
            self._turn_done()

    def _frame(self) -> QPixmap:
        """The current pages drawn once, so a slide only has to move two finished images."""
        ratio = self.devicePixelRatioF()
        frame = QPixmap(self.canvas.size() * ratio)
        frame.setDevicePixelRatio(ratio)
        painter = QPainter(frame)
        self._paint_pages(painter, self.canvas.width(), self.canvas.height())
        painter.end()
        return frame

    def set_zoom(self, zoom: float) -> None:
        self._finish_turn()
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        area = self.viewport().size()
        x = (hbar.value() + area.width() / 2) / max(1, self.canvas.width())
        y = (vbar.value() + area.height() / 2) / max(1, self.canvas.height())
        zoom = max(1.0, min(4.0, zoom))
        self.zoom = 1.0 if zoom < 1.03 else round(zoom, 3)
        self._sync(reset=False)
        hbar.setValue(int(x * self.canvas.width() - area.width() / 2))
        vbar.setValue(int(y * self.canvas.height() - area.height() / 2))
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom > 1.0 else Qt.CursorShape.ArrowCursor)
        self.zoom_changed.emit()

    def _page_size(self) -> QSize:
        area = self.viewport().size()
        columns = 2 if self._spread() else 1
        height = area.height() - 24
        width = int(height / self.PAGE_RATIO)
        fit = (area.width() - 24 - (columns - 1) * 6) / columns
        if width > fit:
            width = int(fit)
            height = int(width * self.PAGE_RATIO)
        return QSize(int(width * self.zoom), int(height * self.zoom))

    def _sync(self, reset: bool = True) -> None:
        if not self.total:
            return
        page = self._page_size()
        columns = 2 if self._spread() else 1
        area = self.viewport().size()
        self.canvas.resize(
            max(area.width(), page.width() * columns + 24), max(area.height(), page.height() + 24))
        if reset:
            self.horizontalScrollBar().setValue(0)
            self.verticalScrollBar().setValue(0)
        visible = self._visible()
        ahead = range(max(visible) + 1, max(visible) + 1 + self.AHEAD)
        behind = range(min(visible) - 1, min(visible) - 1 - self.BEHIND, -1)
        for index in chain(visible, ahead, behind):
            if 0 <= index < self.total:
                self._ensure(index)
        self._ensure_sharp(visible)
        self.canvas.update()

    def _ensure_sharp(self, visible: list[int]) -> None:
        if self.zoom <= 1.15:
            return
        for index in list(self.sharp):
            if index not in visible:
                del self.sharp[index]
        for index in visible:
            if index not in self.sharp and index not in self.sharp_loading:
                self.sharp_loading.add(index)
                self.provider.sharp(index, self._sharp_received)

    def _sharp_received(self, index: int, pixmap: QPixmap | None) -> None:
        self.sharp_loading.discard(index)
        if pixmap is not None and index in self._visible():
            self.sharp[index] = pixmap
            self.canvas.update()

    def _ensure(self, index: int) -> None:
        if index in self.pages or index in self.loading or index in self.failed:
            return
        self.loading.add(index)
        self.provider.request(index, self.render_width, self._received)

    def _received(self, index: int, pixmap: QPixmap | None) -> None:
        self.loading.discard(index)
        if pixmap is None:
            self.failed.add(index)
        else:
            self.pages[index] = pixmap
            while len(self.pages) > self.CACHE:  # Forget whichever page is furthest away.
                del self.pages[max(self.pages, key=lambda page: abs(page - self.index))]
        if index in self._visible():
            self.canvas.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._finish_turn()
        self._sync(reset=False)

    def paint_canvas(self, canvas: QWidget) -> None:
        painter = QPainter(canvas)
        if self.from_frame is not None and self.to_frame is not None:
            width = canvas.width()
            shift = self.slide * width * self.direction
            painter.drawPixmap(round(-shift), 0, self.from_frame)
            painter.drawPixmap(round(self.direction * width - shift), 0, self.to_frame)
            return
        self._paint_pages(painter, canvas.width(), canvas.height())

    def _paint_pages(self, painter: QPainter, canvas_w: int, canvas_h: int) -> None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(0, 0, canvas_w, canvas_h, self.background)
        if not self.total:
            return
        visible = self._visible()
        size = self._page_size()
        total_width = size.width() * len(visible) + (len(visible) - 1) * 6
        x = (canvas_w - total_width) / 2
        y = (canvas_h - size.height()) / 2
        for index in visible:
            rect = QRectF(x, y, size.width(), size.height())
            pixmap = self.sharp.get(index) if self.zoom > 1.15 else None
            if pixmap is None:
                pixmap = self.pages.get(index)
            if pixmap is not None:
                ratio = pixmap.height() / max(1, pixmap.width())
                drawn_h = min(rect.height(), rect.width() * ratio)
                drawn_w = drawn_h / ratio
                target = QRectF(
                    rect.center().x() - drawn_w / 2, rect.center().y() - drawn_h / 2, drawn_w, drawn_h)
                painter.drawPixmap(target.toRect(), pixmap)
            else:
                painter.fillRect(rect, self.placeholder)
                if index in self.failed:
                    painter.setPen(self.ink)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "This page couldn't be loaded")
            x += size.width() + 6

    def canvas_clicked(self, fraction: float) -> None:
        if self.zoom > 1.0:
            return
        if fraction < 0.3:
            self.previous()
        elif fraction > 0.7:
            self.next()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * (1.15 if delta > 0 else 1 / 1.15))
        elif self.zoom > 1.0:
            super().wheelEvent(event)
        else:
            self._wheel = getattr(self, "_wheel", 0) + delta
            if abs(self._wheel) >= 120:
                if self._wheel < 0:
                    self.next()
                else:
                    self.previous()
                self._wheel = 0
