"""Reusable interface pieces: cover cards, shelves, grids and the book menu."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from shiboken6 import isValid

from .context import AppContext
from .theme import theme


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def tool_button(icon: str, tip: str, size: int = 20) -> QToolButton:
    button = QToolButton()
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIconSize(QSize(size, size))

    def paint() -> None:
        if isValid(button):
            button.setIcon(theme.icon(icon, size=size))

    paint()
    theme.changed.connect(paint)
    return button


class BookMenu:
    """The right-click menu for any book."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def popup(self, book: dict, position: QPoint, parent: QWidget) -> None:
        library = self.ctx.library
        stored = library.book(book["id"])
        menu = QMenu(parent)
        menu.addAction("Listen" if book.get("kind") == "audio" else "Read", lambda: self.ctx.read_now(book))
        menu.addAction("Open Details", lambda: self.ctx.show_detail.emit(stored or book))
        kept = stored and (stored["local"] or stored["source"] == "local")
        if not kept and not self.ctx.downloads.is_active(book["id"]):
            menu.addAction("Download", lambda: self.ctx.start_download(book))
        menu.addSeparator()
        wanted = bool(stored and stored["want"])
        menu.addAction("Remove from Want to Read" if wanted else "Add to Want to Read",
                       lambda: self.ctx.toggle_want(book))
        finished = bool(stored and stored["finished_at"])

        def toggle_finished() -> None:
            self.ctx.ensure_in_library(book)
            library.set_finished(book["id"], not finished)

        menu.addAction("Mark as Not Finished" if finished else "Mark as Finished", toggle_finished)
        submenu = menu.addMenu("Add to Collection")
        self.fill_collections(submenu, book, parent)
        self.add_manage_actions(menu, stored, parent)
        menu.exec(position)

    def add_manage_actions(self, menu: QMenu, stored: dict | None, parent: QWidget) -> None:
        if not stored:
            return
        actions = []
        if stored["source"] == "local":
            actions.append(("Remove from Library…", lambda: self.confirm_remove(stored, parent)))
        elif stored["local"]:
            actions.append(("Remove Download…", lambda: self.confirm_remove_download(stored, parent)))
        if stored["last_opened"] and stored["source"] != "local":
            actions.append(("Remove from Continue Reading", lambda: self.ctx.library.clear_recent(stored["id"])))
        if actions:
            menu.addSeparator()
        for text, handler in actions:
            menu.addAction(text, handler)

    def fill_collections(self, menu: QMenu, book: dict, parent: QWidget) -> None:
        library = self.ctx.library
        current = library.book_collections(book["id"]) if library.has_book(book["id"]) else set()
        for collection in library.collections():
            action = menu.addAction(collection["name"])
            action.setCheckable(True)
            action.setChecked(collection["id"] in current)

            def toggle(checked: bool, cid: int = collection["id"]) -> None:
                self.ctx.ensure_in_library(book)
                if checked:
                    library.add_to_collection(cid, book["id"])
                else:
                    library.remove_from_collection(cid, book["id"])

            action.toggled.connect(toggle)
        if library.collections():
            menu.addSeparator()

        def create() -> None:
            name, ok = QInputDialog.getText(parent, "New Collection", "Name:")
            if ok and name.strip():
                self.ctx.ensure_in_library(book)
                library.add_to_collection(library.create_collection(name), book["id"])

        menu.addAction("New Collection…", create)

    def confirm_remove(self, book: dict, parent: QWidget) -> None:
        answer = QMessageBox.question(
            parent, "Remove from Library",
            f"Remove “{book['title']}” from your library?\n\n"
            "The imported file, your reading position, highlights and notes will be deleted.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.downloads.remove(book)
            self.ctx.library.remove_book(book["id"])

    def confirm_remove_download(self, book: dict, parent: QWidget) -> None:
        answer = QMessageBox.question(
            parent, "Remove Download",
            f"Remove “{book['title']}” from your library and delete the offline copy?\n\n"
            "You can still read it online, and your place and notes are kept.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.downloads.remove(book)


class CoverCard(QWidget):
    """A book: its cover on a shared baseline, with quick actions when the pointer is over it."""

    TEXT_HEIGHT = 50
    BUTTON = 30
    TIPS = {"open": "", "want": "", "download": ""}

    def __init__(self, ctx: AppContext, book: dict, width: int = 138, show_progress: bool = False) -> None:
        super().__init__()
        self.ctx = ctx
        self.book = book
        self.show_progress = show_progress
        self.cover_w = width
        self.cover_h = int(width * 1.5)
        self.pixmap: QPixmap | None = None
        self.designed: QPixmap | None = None
        self.art_known = False
        self.announced = False
        self.hover = False
        self.hot = ""
        self.pressed: str | None = None
        self.fraction = 0.0
        self.setFixedSize(width + 12, self.cover_h + self.TEXT_HEIGHT + 12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # A click shouldn't pull focus onto a cover.
        self.setMouseTracking(True)
        self.setAccessibleName(f"{book.get('title')} by {book.get('author')}")
        self.plain_tip = f"{book.get('title')}\n{book.get('author')}"
        self.setToolTip(self.plain_tip)
        self._load_state()
        ctx.library.book_changed.connect(self._book_changed)
        ctx.downloads.progress.connect(self._download_progress)
        ctx.downloads.finished.connect(self._book_changed)
        ctx.downloads.failed.connect(self._book_changed)
        ctx.covers.request(book, self._cover_ready, self)

    def _load_state(self) -> None:
        stored = self.ctx.library.book(self.book["id"])
        self.wanted = bool(stored and stored["want"])
        self.imported = self.book.get("source") == "local" or bool(stored and stored["source"] == "local")
        self.downloaded = bool(stored and stored["local"]) and not self.imported
        self.downloading = self.ctx.downloads.is_active(self.book["id"])
        if not self.downloading:
            self.fraction = 0.0

    def _book_changed(self, book_id: str, _message: str = "") -> None:
        if book_id == self.book["id"]:
            self._load_state()
            self.update()

    def _download_progress(self, book_id: str, fraction: float) -> None:
        if book_id == self.book["id"]:
            self.fraction = fraction
            self.downloading = True
            self.update()

    def _cover_ready(self, pixmap: QPixmap | None) -> None:
        self.pixmap = pixmap
        self.art_known = True
        self.update()

    def _art(self) -> QPixmap | None:
        if self.pixmap is not None:
            return self.pixmap
        if not self.art_known:
            return None
        if self.designed is None:  # Drawn only for the covers that really need one.
            self.designed = self.ctx.covers.fallback(self.book, self.cover_w, self.cover_h)
        return self.designed

    def enterEvent(self, event) -> None:
        self.hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self.hover = False
        self.hot = ""
        self.update()

    def mouseMoveEvent(self, event) -> None:
        hot = self._target_at(event.position())
        if hot != self.hot:
            self.hot = hot
            self.setToolTip(self._tip(hot))
            self.update()

    def _tip(self, target: str) -> str:
        audio = self.book.get("kind") == "audio"
        if target == "open":
            return "Listen now, without adding it to your library" if audio \
                else "Read now, without adding it to your library"
        if target == "want":
            return "Remove from Want to Read" if self.wanted else "Add to Want to Read"
        if target == "download":
            if self.downloading:
                return "Cancel download"
            return "Downloaded. Press to remove it." if self.downloaded else "Download to your library"
        return self.plain_tip

    def _target_at(self, position) -> str:
        rect = self._shown_rect()
        for name, target in self._targets(rect).items():
            if target.contains(position):
                return name
        return ""

    def mousePressEvent(self, event) -> None:
        self.pressed = self._target_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None

    def mouseReleaseEvent(self, event) -> None:
        pressed, self.pressed = self.pressed, None
        if event.button() != Qt.MouseButton.LeftButton or not self.rect().contains(event.position().toPoint()):
            return
        target = self._target_at(event.position())
        if pressed != target:  # Only a press and release on the same spot counts as a click.
            return
        if target == "open":
            self.ctx.read_now(self.book)
        elif target == "want":
            self.ctx.toggle_want(self.book)
        elif target == "download":
            self._download_clicked()
        else:
            self.ctx.show_detail.emit(self.book)

    def _download_clicked(self) -> None:
        if self.downloading:
            self.ctx.downloads.cancel(self.book["id"])
            self.downloading = False
            self.update()
        elif self.downloaded:
            stored = self.ctx.library.book(self.book["id"])
            if stored:
                BookMenu(self.ctx).confirm_remove_download(stored, self)
        else:
            self.downloading = True
            self.update()
            self.ctx.start_download(self.book)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.ctx.show_detail.emit(self.book)
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        BookMenu(self.ctx).popup(self.book, event.globalPos(), self)

    def _cover_rect(self) -> QRect:
        art = self._art()
        ratio = art.height() / art.width() if art is not None and art.width() else 1.5
        height = min(self.cover_h, int(self.cover_w * ratio))
        return QRect(6, 6 + self.cover_h - height, self.cover_w, height)

    def _shown_rect(self) -> QRect:
        rect = self._cover_rect()
        return rect.translated(0, -3) if self.hover or self.hasFocus() else rect

    def _targets(self, rect: QRect) -> dict[str, QRectF]:
        area = QRectF(rect)
        size = self.BUTTON
        x = area.right() - size - 5
        out: dict[str, QRectF] = {}
        if not self.imported:
            out["download"] = QRectF(x, area.top() + 6, size, size)
            x -= size + 5
        out["want"] = QRectF(x, area.top() + 6, size, size)
        out["open"] = QRectF(area.center().x() - 36, area.bottom() - 42, 72, 30)
        return out

    def _draw_button(self, painter: QPainter, area: QRectF, icon: str, hot: bool) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("accent") if hot else QColor(0, 0, 0, 165))
        painter.drawEllipse(area)
        theme.icon(icon, "#FFFFFF", 16).paint(painter, area.adjusted(7, 7, -7, -7).toRect())

    def _paint_actions(self, painter: QPainter, rect: QRect, clip: QPainterPath) -> None:
        targets = self._targets(rect)
        if self.hover:
            painter.save()
            painter.setClipPath(clip)
            painter.fillRect(rect, QColor(0, 0, 0, 34))
            painter.restore()
        if "download" in targets and (self.hover or self.downloading or self.downloaded):
            area = targets["download"]
            if self.downloading:
                self._draw_button(painter, area, "close", self.hot == "download")
                pen = QPen(QColor("#FFFFFF"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(area.adjusted(2.5, 2.5, -2.5, -2.5), 90 * 16,
                                int(-max(0.04, self.fraction) * 360 * 16))
            else:
                self._draw_button(painter, area, "check" if self.downloaded else "download", self.hot == "download")
        if self.hover or self.wanted:
            self._draw_button(painter, targets["want"], "want_fill" if self.wanted else "want", self.hot == "want")
        if self.hover:
            pill = targets["open"]
            hot = self.hot == "open"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("accent") if hot else QColor("#FFFFFF"))
            painter.drawRoundedRect(pill, 15, 15)
            font = self.font()
            font.setWeight(font.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF") if hot else QColor("#1C1C1E"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, "Listen" if self.book.get("kind") == "audio" else "Read")

    def paintEvent(self, event) -> None:
        if not self.announced:
            self.announced = True
            self.ctx.covers.bump(self.book)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = self._shown_rect()
        art = self._art()
        # A soft stacked shadow lifts the book off the page.
        steps = 5 if self.hover else 4
        for i in range(steps, 0, -1):
            shadow = QRectF(rect).adjusted(-i * 0.5, i * 0.2, i * 0.5, i * 1.1)
            alpha = 16 if theme.dark else 11
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow, 5, 5)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(rect), 4, 4)
        painter.save()
        painter.setClipPath(clip)
        if art is not None:
            painter.drawPixmap(rect, art)
        else:
            painter.fillRect(rect, theme.color("skeleton"))
        painter.restore()
        if self.book.get("kind") == "audio":
            badge = QRectF(rect.right() - 27, rect.bottom() - 27, 22, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawEllipse(badge)
            theme.icon("headphones", "#FFFFFF", 14).paint(painter, badge.adjusted(4, 4, -4, -4).toRect())
        self._paint_actions(painter, rect, clip)
        if self.hasFocus():
            painter.setPen(theme.color("accent"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(-2, -2, 2, 2), 5, 5)

        top = 6 + self.cover_h + 8
        width = self.cover_w
        font = self.font()
        font.setWeight(font.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        painter.setPen(theme.color("text"))
        title = metrics.elidedText(self.book.get("title", ""), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(6, top, width, metrics.height()), Qt.AlignmentFlag.AlignLeft, title)
        small = self.font()
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        painter.setFont(small)
        painter.setPen(theme.color("text2"))
        y = top + metrics.height() + 2
        small_metrics = QFontMetrics(small)
        progress = self.book.get("progress") or 0
        if self.show_progress and progress > 0:
            bar_w = width - 40
            bar = QRectF(6, y + 5, bar_w, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("track"))
            painter.drawRoundedRect(bar, 2, 2)
            painter.setBrush(theme.color("accent"))
            painter.drawRoundedRect(QRectF(6, y + 5, max(4.0, bar_w * progress), 4), 2, 2)
            painter.setPen(theme.color("text2"))
            painter.drawText(
                QRect(6 + bar_w + 6, y, 34, small_metrics.height()), Qt.AlignmentFlag.AlignLeft,
                f"{round(progress * 100)}%")
        else:
            author = small_metrics.elidedText(self.book.get("author", ""), Qt.TextElideMode.ElideRight, width)
            painter.drawText(QRect(6, y, width, small_metrics.height()), Qt.AlignmentFlag.AlignLeft, author)

    def sizeHint(self) -> QSize:
        return self.size()


class CountUp(QLabel):
    """A large number that counts up the first time it's shown."""

    def __init__(self, placeholder: str, name: str) -> None:
        super().__init__(placeholder)
        self.setObjectName(name)
        self.value = 0
        self.played = False
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(1100)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda v: self.setText(f"{int(v * self.value):,}"))

    def set_value(self, value: int) -> None:
        self.value = value
        if self.played:
            self.setText(f"{value:,}")
        elif self.isVisible():
            self._play()

    def _play(self) -> None:
        self.played = True
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.value and not self.played:
            self._play()


class _HorizontalScroll(QScrollArea):
    """Lets ordinary vertical wheel movement pass through to the page."""

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        if abs(delta.y()) > abs(delta.x()):
            event.ignore()
        else:
            super().wheelEvent(event)


class _Skeleton(QWidget):
    def __init__(self, width: int = 138) -> None:
        super().__init__()
        self.setFixedSize(width + 12, int(width * 1.5) + 62)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("skeleton"))
        width = self.width() - 12
        height = int(width * 1.5)
        painter.drawRoundedRect(QRectF(6, 6, width, height), 4, 4)
        painter.drawRoundedRect(QRectF(6, height + 16, width * 0.8, 10), 3, 3)
        painter.drawRoundedRect(QRectF(6, height + 33, width * 0.5, 8), 3, 3)


class Shelf(QWidget):
    """A titled, horizontally scrolling row of books."""

    loaded = Signal(bool)  # True if the network request succeeded.

    def __init__(self, ctx: AppContext, title: str, see_all: Callable[[], None] | None = None,
                 show_progress: bool = False) -> None:
        super().__init__()
        self.ctx = ctx
        self.show_progress = show_progress
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        self.title = label(title, "H2")
        header.addWidget(self.title)
        if see_all:
            button = QPushButton("See All")
            button.setObjectName("Link")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(see_all)
            header.addWidget(button)
        header.addStretch(1)
        self.left = tool_button("back", "Scroll left", 16)
        self.right = tool_button("forward", "Scroll right", 16)
        self.left.clicked.connect(lambda: self._page(-1))
        self.right.clicked.connect(lambda: self._page(1))
        header.addWidget(self.left)
        header.addWidget(self.right)
        layout.addLayout(header)
        self.scroll = _HorizontalScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.row_widget = QWidget()
        self.row = QHBoxLayout(self.row_widget)
        self.row.setContentsMargins(0, 2, 0, 2)
        self.row.setSpacing(12)
        self.row.addStretch(1)
        self.scroll.setWidget(self.row_widget)
        self.scroll.setFixedHeight(int(138 * 1.5) + 72)
        layout.addWidget(self.scroll)

    def show_skeleton(self, count: int = 10) -> None:
        self._clear()
        for index in range(count):
            self.row.insertWidget(index, _Skeleton())

    def _clear(self) -> None:
        while self.row.count() > 1:
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_books(self, books: list[dict]) -> None:
        self._clear()
        for index, book in enumerate(books):
            self.row.insertWidget(index, CoverCard(self.ctx, book, show_progress=self.show_progress))
        self.setVisible(bool(books))

    def load(self, query: str, sort: str = "downloads desc", rows: int = 24,
             exclude: set[str] | None = None) -> None:
        self.show_skeleton()

        def received(books: list[dict], _total: int, error: str) -> None:
            if error:
                self.setVisible(False)
                self.loaded.emit(False)
                return
            shown = [b for b in books if not exclude or b["id"] not in exclude]
            self.set_books(shown[:rows])
            self.loaded.emit(True)

        self.ctx.search.run(query, received, self, sort=sort, rows=rows + 16)

    def _page(self, direction: int) -> None:
        bar = self.scroll.horizontalScrollBar()
        bar.setValue(bar.value() + direction * max(300, int(self.scroll.width() * 0.8)))


class FlowGrid(QWidget):
    """Wraps equally sized children into as many columns as fit."""

    def __init__(self, spacing: int = 14) -> None:
        super().__init__()
        self.spacing = spacing
        self.items: list[QWidget] = []

    def set_items(self, items: list[QWidget]) -> None:
        focused = self.window().focusWidget()
        if focused in self.items:  # Otherwise Qt moves focus to another cover and scrolls to it.
            self.setFocus()
        for item in self.items:
            item.deleteLater()
        self.items = []
        self.add_items(items)

    def add_items(self, items: list[QWidget]) -> None:
        for item in items:
            item.setParent(self)
            item.show()
        self.items.extend(items)
        self._layout()

    def resizeEvent(self, event) -> None:
        self._layout()

    def _layout(self) -> None:
        if not self.items:
            self.setFixedHeight(0)
            return
        cell = self.items[0].size()
        step_x = cell.width() + self.spacing
        step_y = cell.height() + self.spacing
        columns = max(1, (self.width() + self.spacing) // step_x)
        for index, item in enumerate(self.items):
            row, column = divmod(index, columns)
            item.move(column * step_x, row * step_y)
        rows = (len(self.items) + columns - 1) // columns
        self.setFixedHeight(rows * step_y)


class Page(QScrollArea):
    """A vertically scrolling screen with standard margins."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("PageContent")
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(36, 30, 36, 36)
        self.body.setSpacing(22)
        self.setWidget(self.content)


class EmptyState(QWidget):
    def __init__(self, title: str, detail: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 70, 0, 70)
        layout.setSpacing(6)
        self.title = label(title, "H2")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail = label(detail, "Muted", wrap=True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)

    def set_text(self, title: str, detail: str = "") -> None:
        self.title.setText(title)
        self.detail.setText(detail)


def expanding() -> QWidget:
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer


def debounce(owner: QWidget, milliseconds: int, function: Callable[[], None]) -> QTimer:
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.setInterval(milliseconds)
    timer.timeout.connect(function)
    return timer
