"""The reading screen: toolbar, side panel, text and scan views, and position tracking."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QStackedWidget,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from .bookfile import BaseBook, BookError, Chapter, EpubBook, TextBook, parse_ocr_pages
from .context import AppContext
from .net import Downloads, FileDownload
from .scanview import PdfPages, RemotePages, ScanView, stream_width
from .textview import ReaderEngine, TextView
from .theme import format_bytes, theme

READER_THEMES = {
    "white": {"bg": "#FFFFFF", "fg": "#1C1C1E", "line": "#E8E8E4", "muted": "#6E6E73", "link": "#0F766E", "card": "#F6F6F3", "name": "White"},
    "sepia": {"bg": "#F6EFE1", "fg": "#3A2E22", "line": "#E4D9C4", "muted": "#7C6A54", "link": "#8A4B1F", "card": "#EFE6D4", "name": "Sepia"},
    "gray": {"bg": "#3F3F43", "fg": "#E8E8EA", "line": "#55555A", "muted": "#B4B4B8", "link": "#7FE3D4", "card": "#4A4A4F", "name": "Gray"},
    "night": {"bg": "#121214", "fg": "#CFCFD3", "line": "#2B2B2E", "muted": "#8A8A90", "link": "#2DD4BF", "card": "#1C1C1F", "name": "Night"},
}
READER_FONTS = {
    "Serif": '"Iowan Old Style","Palatino Linotype","Noto Serif",Georgia,"DejaVu Serif",serif',
    "Georgia": 'Georgia,"Liberation Serif","Noto Serif",serif',
    "Sans": '"Noto Sans",Inter,"Segoe UI","Helvetica Neue",Arial,sans-serif',
}
HIGHLIGHT_COLORS = {"yellow": "#FACC15", "green": "#4ADE80", "blue": "#60A5FA", "pink": "#F472B6"}
DEFAULTS = {
    "theme": "white", "font": "Serif", "size": 19, "line_height": 1.55, "margin": 56,
    "justify": False, "mode": "paged", "spread": True, "animate": True,
}
IDLE_SECONDS = 300
TICK_SECONDS = 15
CHAPTERS_KEPT = 8


class AppearancePopup(QFrame):
    changed = Signal()

    def __init__(self, reader: "ReaderPage") -> None:
        super().__init__(reader, Qt.WindowType.Popup)
        self.reader = reader
        self.setObjectName("Popup")
        self.setFixedWidth(290)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        swatches = QHBoxLayout()
        self.theme_group = QButtonGroup(self)
        for key, palette in READER_THEMES.items():
            button = QPushButton(palette["name"])
            button.setCheckable(True)
            button.setChecked(reader.settings["theme"] == key)
            button.setFixedHeight(40)
            button.setStyleSheet(
                f"QPushButton{{background:{palette['bg']};color:{palette['fg']};border:1px solid {palette['line']};"
                "border-radius:8px;padding:0}"
                f"QPushButton:checked{{border:2px solid {theme['accent']}}}"
            )
            button.clicked.connect(lambda _=False, k=key: self._set("theme", k))
            self.theme_group.addButton(button)
            swatches.addWidget(button)
        layout.addLayout(swatches)

        self.text_controls = QWidget()
        text = QVBoxLayout(self.text_controls)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(10)
        size_row = QHBoxLayout()
        smaller = QPushButton("A−")
        smaller.setToolTip("Smaller text")
        smaller.clicked.connect(lambda: self._set("size", max(12, reader.settings["size"] - 1)))
        larger = QPushButton("A+")
        larger.setToolTip("Larger text")
        larger.clicked.connect(lambda: self._set("size", min(36, reader.settings["size"] + 1)))
        size_row.addWidget(smaller)
        size_row.addWidget(larger)
        text.addLayout(size_row)
        self._combo(text, "Font", [(k, k) for k in READER_FONTS], "font")
        self._combo(text, "Line spacing", [("Tight", 1.35), ("Normal", 1.55), ("Loose", 1.8)], "line_height")
        self._combo(text, "Margins", [("Narrow", 28), ("Normal", 56), ("Wide", 110)], "margin")
        self._combo(text, "Layout", [("Pages", "paged"), ("Scrolling", "scroll")], "mode")
        spread = QCheckBox("Two pages when the window is wide")
        spread.setChecked(reader.settings["spread"])
        spread.toggled.connect(lambda v: self._set("spread", v))
        text.addWidget(spread)
        justify = QCheckBox("Justify text")
        justify.setChecked(reader.settings["justify"])
        justify.toggled.connect(lambda v: self._set("justify", v))
        text.addWidget(justify)
        layout.addWidget(self.text_controls)
        animate = QCheckBox("Animate page turns")
        animate.setChecked(reader.settings["animate"])
        animate.toggled.connect(lambda v: self._set("animate", v))
        layout.addWidget(animate)

    def _combo(self, parent: QVBoxLayout, caption: str, items: list, key: str) -> QComboBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(caption))
        row.addStretch(1)
        combo = QComboBox()
        for text, value in items:
            combo.addItem(text, value)
        combo.setCurrentIndex(max(0, combo.findData(self.reader.settings[key])))
        combo.activated.connect(lambda: self._set(key, combo.currentData()))
        row.addWidget(combo)
        parent.addLayout(row)
        return combo

    def _set(self, key: str, value) -> None:
        self.reader.settings[key] = value
        self.changed.emit()


class _NoteEditor(QTextEdit):
    """A note field that grows with what's written in it."""

    MINIMUM = 30

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setAcceptRichText(False)
        self.setPlaceholderText("Add a note…")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setPlainText(text)
        self.textChanged.connect(self.fit)
        self.setFixedHeight(self.MINIMUM)

    def fit(self) -> None:
        self.document().setTextWidth(self.viewport().width())
        self.setFixedHeight(max(self.MINIMUM, int(self.document().size().height()) + 6))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def wheelEvent(self, event) -> None:
        event.ignore()  # Let the list of notes scroll.


class _NoteCard(QFrame):
    """One highlight or bookmark: where it is, what it marks, and an editable note."""

    def __init__(self, reader: "ReaderPage", kind: str, record: dict, location: str, reachable: bool) -> None:
        super().__init__()
        self.setObjectName("NoteCard")
        self.reader = reader
        self.kind = kind
        self.record = record
        self.reachable = reachable
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 6, 6)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(7)
        palette = READER_THEMES[reader.settings["theme"]]
        if kind == "highlight":
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(
                f"background:{HIGHLIGHT_COLORS.get(record['color'], HIGHLIGHT_COLORS['yellow'])};border-radius:5px;")
            head.addWidget(dot)
        else:
            mark = QLabel()
            mark.setPixmap(theme.icon("bookmark", palette["muted"], 14).pixmap(14, 14))
            head.addWidget(mark)
        self.where = QPushButton(location)
        self.where.setObjectName("NoteWhere")
        self.where.setToolTip("Go to this place" if reachable else "This was made in a text view that no longer exists")
        self.where.setEnabled(reachable)
        self.where.setCursor(Qt.CursorShape.PointingHandCursor)
        self.where.clicked.connect(lambda: reader._go_to_note(self))
        head.addWidget(self.where, 1, Qt.AlignmentFlag.AlignLeft)
        remove = QToolButton()
        remove.setToolTip("Remove highlight" if kind == "highlight" else "Remove bookmark")
        remove.setAccessibleName(remove.toolTip())
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setIcon(theme.icon("close", palette["muted"], 14))
        remove.setIconSize(QSize(14, 14))
        remove.clicked.connect(lambda: reader._remove_note(self))
        head.addWidget(remove)
        layout.addLayout(head)
        if kind == "highlight":
            quote = QLabel(f"“{record['text'][:260].strip()}”")
            quote.setObjectName("NoteQuote")
            quote.setWordWrap(True)
            quote.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(quote)
        self.editor = _NoteEditor(record["note"])
        layout.addWidget(self.editor)
        self.timer = QTimer(self, singleShot=True, interval=350, timeout=self.save)
        self.editor.textChanged.connect(self.timer.start)

    def save(self) -> None:
        text = self.editor.toPlainText().strip()
        if text != self.record["note"]:
            self.record["note"] = text
            self.reader._note_edited(self)


class ReaderPage(QWidget):
    closed = Signal()

    def __init__(self, ctx: AppContext, engine: ReaderEngine, book: dict) -> None:
        super().__init__()
        self.ctx = ctx
        self.engine = engine
        self.book = book
        self.plan = book.get("plan") or {}
        self.settings = {**DEFAULTS, **(ctx.library.setting("reader", {}) or {})}
        self.tinted: list[tuple[QToolButton, str]] = []

        self.view_name: str | None = None
        self.wanted_view: str | None = None
        self.token = uuid.uuid4().hex
        self.text_book: BaseBook | None = None
        self.text_view: TextView | None = None
        self.scan_view: ScanView | None = None
        self.provider = None
        self.pending: FileDownload | None = None
        self.progress_value = float(book.get("progress") or 0)
        self.spine = 0
        self.offset = 0
        self.page_start = 0
        self.end_offset = 0
        self.scan_page = 0
        self.ocr_pages: list[str] | None = None
        self.last_active = time.monotonic()
        self.search_results: list[dict] = []
        self.slider_active = False
        self.chapters: dict[int, Chapter] = {}
        self.note_cards: list[_NoteCard] = []
        self.shut_down = False

        self._build()
        self._apply_chrome()
        self._render_download()
        self.save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save)
        self.tick = QTimer(self, interval=TICK_SECONDS * 1000, timeout=self._tick)
        self.tick.start()
        ctx.downloads.progress.connect(self._download_progress)
        ctx.downloads.finished.connect(self._download_ended)
        ctx.downloads.failed.connect(self._download_ended)

        ctx.library.mark_opened(book["id"])
        self.open_view(self.plan.get("default_view"))

    # Building the screen --------------------------------------------------------
    def _button(self, icon: str, tip: str, handler, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setIconSize(QSize(20, 20))
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(handler)
        self.tinted.append((button, icon))
        return button

    def _build(self) -> None:
        self.setObjectName("ReaderRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("ReaderBar")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(2)
        bar.addWidget(self._button("back", "Back to your books (Alt+Left)", self.leave))
        bar.addSpacing(6)
        self.contents_button = self._button("list", "Contents", lambda: self._panel(0), True)
        self.contents_button.hide()  # Shown only for books that have a table of contents.
        bar.addWidget(self.contents_button)
        self.search_button = self._button("search", "Search in book (Ctrl+F)", lambda: self._panel(1), True)
        bar.addWidget(self.search_button)
        self.notes_button = self._button("note", "Notes and bookmarks", lambda: self._panel(2), True)
        bar.addWidget(self.notes_button)
        bar.addStretch(1)
        self.title_label = QLabel(self.book["title"][:70])
        self.title_label.setObjectName("ReaderTitle")
        bar.addWidget(self.title_label)
        bar.addStretch(1)
        self.source_button = QPushButton()
        self.source_button.setObjectName("SourceChip")
        self.source_button.setIconSize(QSize(15, 15))
        self.source_button.clicked.connect(self._source_clicked)
        bar.addWidget(self.source_button)
        bar.addSpacing(6)
        self.bookmark_button = self._button("bookmark", "Bookmark this page (Ctrl+D)", self._toggle_bookmark, True)
        bar.addWidget(self.bookmark_button)
        self.appearance_button = self._button("type", "Appearance", self._show_appearance)
        bar.addWidget(self.appearance_button)
        bar.addWidget(self._button("fullscreen", "Full screen (F11)", self._toggle_fullscreen))
        outer.addWidget(self.toolbar)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        self.stack = QStackedWidget()
        self.loading = QWidget()
        loading_layout = QVBoxLayout(self.loading)
        loading_layout.addStretch(1)
        self.loading_label = QLabel("Opening book…")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.loading_label.setWordWrap(True)
        loading_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setMaximumWidth(280)
        self.loading_bar.setRange(0, 0)
        loading_layout.addWidget(self.loading_bar, 0, Qt.AlignmentFlag.AlignHCenter)
        self.retry = QPushButton("Try Again")
        self.retry.clicked.connect(lambda: self.open_view(self.wanted_view))
        self.retry.hide()
        loading_layout.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch(1)
        self.stack.addWidget(self.loading)
        middle.addWidget(self.stack, 1)

        self.panel = QWidget()
        self.panel.setObjectName("ReaderPanel")
        self.panel.setFixedWidth(340)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(10, 12, 10, 10)
        self.panel_title = QLabel("Contents")
        self.panel_title.setObjectName("PanelTitle")
        panel_layout.addWidget(self.panel_title)
        self.panel_stack = QStackedWidget()
        self.toc_list = QListWidget()
        self.toc_list.itemActivated.connect(self._toc_chosen)
        self.toc_list.itemClicked.connect(self._toc_chosen)
        self.panel_stack.addWidget(self.toc_list)

        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Find in this book")
        self.search_box.returnPressed.connect(self._search)
        search_layout.addWidget(self.search_box)
        self.search_status = QLabel("")
        self.search_status.setObjectName("Muted")
        self.search_status.setWordWrap(True)
        search_layout.addWidget(self.search_status)
        self.search_list = QListWidget()
        self.search_list.setWordWrap(True)
        self.search_list.itemActivated.connect(self._hit_chosen)
        self.search_list.itemClicked.connect(self._hit_chosen)
        search_layout.addWidget(self.search_list, 1)
        self.panel_stack.addWidget(search_page)

        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.notes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.notes_holder = QWidget()
        self.notes_holder.setObjectName("NotesHolder")
        self.notes_layout = QVBoxLayout(self.notes_holder)
        self.notes_layout.setContentsMargins(0, 0, 4, 0)
        self.notes_layout.setSpacing(8)
        self.notes_layout.addStretch(1)
        self.notes_empty = QLabel()
        self.notes_empty.setObjectName("Muted")
        self.notes_empty.setWordWrap(True)
        self.notes_layout.insertWidget(0, self.notes_empty)
        self.notes_scroll.setWidget(self.notes_holder)
        self.panel_stack.addWidget(self.notes_scroll)
        panel_layout.addWidget(self.panel_stack, 1)
        self.panel.hide()
        middle.addWidget(self.panel)
        outer.addLayout(middle, 1)

        self.footer = QWidget()
        self.footer.setObjectName("ReaderBar")
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(18, 4, 18, 8)
        foot.setSpacing(14)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName("Position in book")
        self.slider.sliderPressed.connect(lambda: setattr(self, "slider_active", True))
        self.slider.sliderReleased.connect(self._slider_released)
        foot.addWidget(self.slider, 1)
        zoom = QHBoxLayout()
        zoom.setSpacing(0)
        self.zoom_out = self._button("minus", "Zoom out (Ctrl+-)", lambda: self._adjust_size(-1))
        self.zoom_value = QPushButton("100%")
        self.zoom_value.setObjectName("ZoomValue")
        self.zoom_value.setToolTip("Back to 100%")
        self.zoom_value.setFixedWidth(50)
        self.zoom_value.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_value.clicked.connect(self._reset_zoom)
        self.zoom_in = self._button("plus", "Zoom in (Ctrl++)", lambda: self._adjust_size(1))
        for widget in (self.zoom_out, self.zoom_value, self.zoom_in):
            zoom.addWidget(widget)
        foot.addLayout(zoom)
        self.position_label = QLabel("")
        self.position_label.setObjectName("ReaderMuted")
        self.position_label.setMinimumWidth(190)
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(self.position_label)
        outer.addWidget(self.footer)

        for keys, handler in (
            ("Right", self.next_page), ("Space", self.next_page), ("PgDown", self.next_page),
            ("Left", self.prev_page), ("PgUp", self.prev_page), ("Shift+Space", self.prev_page),
            ("Ctrl+F", lambda: self._panel(1, force=True)), ("F11", self._toggle_fullscreen),
            ("Ctrl+D", self._toggle_bookmark), ("Escape", self._escape), ("Alt+Left", self.leave),
            ("Ctrl+=", lambda: self._adjust_size(1)), ("Ctrl++", lambda: self._adjust_size(1)),
            ("Ctrl+-", lambda: self._adjust_size(-1)),
        ):
            QShortcut(QKeySequence(keys), self, activated=handler)

    def _apply_chrome(self) -> None:
        palette = READER_THEMES[self.settings["theme"]]
        bg, fg, line, muted, card = palette["bg"], palette["fg"], palette["line"], palette["muted"], palette["card"]
        accent = palette["link"]
        self.setStyleSheet(f"""
        #ReaderRoot, #ReaderBar, #ReaderPanel, #NotesHolder, QStackedWidget, QStackedWidget > QWidget {{ background:{bg}; color:{fg}; }}
        #ReaderPanel {{ border-left:1px solid {line}; }}
        #ReaderTitle {{ color:{muted}; font-size:13px; }}
        #ReaderMuted, #Muted {{ color:{muted}; font-size:12px; }}
        #PanelTitle {{ color:{fg}; font-family:{theme.serif_css()}; font-size:18px; font-weight:600; padding:0 4px 8px; }}
        QLabel {{ color:{fg}; }}
        QToolButton:hover {{ background:{line}; }}
        QToolButton:checked {{ background:{line}; }}
        QPushButton#ZoomValue {{ background:transparent; color:{muted}; border:none; padding:2px; font-size:12px; }}
        QPushButton#ZoomValue:hover {{ color:{fg}; }}
        QPushButton#SourceChip {{ background:transparent; color:{accent}; border:1px solid {line}; border-radius:8px; padding:4px 10px; font-size:12px; }}
        QPushButton#SourceChip:hover {{ background:{line}; }}
        QPushButton#SourceChip:disabled {{ color:{muted}; border-color:transparent; background:transparent; }}
        #NoteCard {{ background:{card}; border-radius:10px; }}
        QPushButton#NoteWhere {{ background:transparent; border:none; padding:2px 0; color:{accent}; font-size:12px; font-weight:600; }}
        QPushButton#NoteWhere:hover {{ text-decoration:underline; }}
        QPushButton#NoteWhere:disabled {{ color:{muted}; }}
        #NoteQuote {{ color:{fg}; font-family:{theme.serif_css()}; font-size:14px; padding-right:8px; }}
        #NoteCard QTextEdit {{ background:transparent; color:{fg}; border:none; font-size:13px; padding:0; selection-background-color:{accent}; }}
        QListWidget {{ color:{fg}; }}
        QListWidget::item {{ color:{fg}; }}
        QListWidget::item:hover {{ background:{line}; }}
        QListWidget::item:selected {{ background:{line}; color:{fg}; }}
        QLineEdit {{ background:{bg}; color:{fg}; border:1px solid {line}; }}
        QSlider::groove:horizontal {{ background:{line}; height:3px; }}
        QSlider::sub-page:horizontal {{ background:{accent}; }}
        QSlider::handle:horizontal {{ background:{accent}; width:10px; height:10px; margin:-4px 0; border-radius:5px; }}
        QScrollBar::handle:vertical {{ background:{line}; }}
        #Popup {{ background:{theme['raised']}; border:1px solid {theme['line']}; border-radius:10px; }}
        #Popup QLabel, #Popup QCheckBox {{ color:{theme['text']}; }}
        """)
        for button, icon in self.tinted:
            button.setIcon(theme.icon(icon, fg, 20))
        if self.scan_view:
            self.scan_view.set_colors(bg, line, muted)
            self.scan_view.animate = self.settings["animate"]
        self._render_download()

    def _settings_payload(self) -> dict:
        palette = READER_THEMES[self.settings["theme"]]
        return {
            "bg": palette["bg"], "fg": palette["fg"], "link": palette["link"], "line": palette["line"],
            "size": self.settings["size"], "lineHeight": self.settings["line_height"],
            "font": READER_FONTS.get(self.settings["font"], READER_FONTS["Serif"]),
            "justify": self.settings["justify"], "mode": self.settings["mode"],
            "spread": self.settings["spread"], "margin": self.settings["margin"],
            "animate": self.settings["animate"],
        }

    def _settings_changed(self) -> None:
        self.ctx.library.set_setting("reader", self.settings)
        self._apply_chrome()
        self._sync_zoom()
        if self.text_view:
            self.text_view.set_background(READER_THEMES[self.settings["theme"]]["bg"])
            if self.text_view.is_ready:
                self.text_view.call("applySettings", self._settings_payload())
        if self.panel.isVisible() and self.panel_stack.currentIndex() == 2:
            self._fill_notes()

    def _adjust_size(self, delta: int) -> None:
        if self.view_name == "text":
            self.settings["size"] = max(12, min(36, self.settings["size"] + delta))
            self._settings_changed()
        elif self.scan_view:
            self.scan_view.set_zoom(self.scan_view.zoom * (1.25 if delta > 0 else 1 / 1.25))

    def _reset_zoom(self) -> None:
        if self.view_name == "text":
            self.settings["size"] = DEFAULTS["size"]
            self._settings_changed()
        elif self.scan_view:
            self.scan_view.set_zoom(1.0)

    def _sync_zoom(self) -> None:
        """The footer shows page zoom on scans, and text size relative to normal on text."""
        if self.view_name == "text":
            size = self.settings["size"]
            percent, low, high = round(size / DEFAULTS["size"] * 100), size <= 12, size >= 36
        elif self.scan_view:
            zoom = self.scan_view.zoom
            percent, low, high = round(zoom * 100), zoom <= 1.0, zoom >= 4.0
        else:
            return
        self.zoom_value.setText(f"{percent}%")
        self.zoom_out.setEnabled(not low)
        self.zoom_in.setEnabled(not high)

    def _show_appearance(self) -> None:
        popup = AppearancePopup(self)
        popup.text_controls.setVisible(self.view_name == "text")
        popup.changed.connect(self._settings_changed)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.adjustSize()
        anchor = self.appearance_button.mapToGlobal(self.appearance_button.rect().bottomRight())
        popup.move(anchor.x() - popup.width(), anchor.y() + 4)
        popup.show()

    def _toggle_fullscreen(self) -> None:
        window = self.window()
        window.showNormal() if window.isFullScreen() else window.showFullScreen()

    def _escape(self) -> None:
        if self.window().isFullScreen():
            self.window().showNormal()
        elif self.panel.isVisible():
            self._hide_panel()

    # Streaming or downloaded -----------------------------------------------------
    def _current_book(self) -> dict:
        return self.ctx.library.book(self.book["id"]) or self.book

    def _render_download(self) -> None:
        book = self._current_book()
        accent = READER_THEMES[self.settings["theme"]]["link"]
        muted = READER_THEMES[self.settings["theme"]]["muted"]
        files = Downloads.files_for(self.plan)
        chip = self.source_button
        chip.setVisible(book.get("source") != "local")
        chip.setEnabled(True)
        if self.ctx.downloads.is_active(book["id"]):
            chip.setIcon(theme.icon("download", accent, 15))
            if not chip.text().startswith("Downloading"):
                chip.setText("Downloading…")
            chip.setToolTip("Press to cancel the download.")
        elif book.get("local"):
            chip.setIcon(theme.icon("check", muted, 15))
            chip.setText("Downloaded")
            chip.setToolTip("This book is in your library and can be read offline.")
            chip.setEnabled(False)
        elif files:
            size = sum(f[2] for f in files)
            chip.setIcon(theme.icon("cloud", accent, 15))
            chip.setText("Streaming · Download")
            chip.setToolTip(f"You're reading online. Download ({format_bytes(size)}) to add this book "
                            "to your library and read it offline.")
        else:
            chip.setIcon(theme.icon("cloud", muted, 15))
            chip.setText("Streaming")
            chip.setToolTip("You're reading online. This book has no downloadable copy.")
            chip.setEnabled(False)

    def _source_clicked(self) -> None:
        book_id = self.book["id"]
        if self.ctx.downloads.is_active(book_id):
            self.ctx.downloads.cancel(book_id)
        else:
            self.ctx.downloads.start(self._current_book(), self.plan)
            self._render_download()

    def _stream_progress(self, ready: int, total: int) -> None:
        if not self._current_book().get("local") and not self.ctx.downloads.is_active(self.book["id"]):
            done = "All pages are ready." if ready >= total else f"{ready:,} of {total:,} pages ready ahead of you."
            self.source_button.setToolTip(f"You're reading online. {done} Press to download this book to your library.")

    def _download_progress(self, book_id: str, fraction: float) -> None:
        if book_id == self.book["id"]:
            self.source_button.setText(f"Downloading {round(fraction * 100)}%")

    def _download_ended(self, book_id: str, _message: str = "") -> None:
        if book_id == self.book["id"]:
            self._render_download()

    # Opening the book -----------------------------------------------------------
    def _status(self, text: str, busy: bool = True, retry: bool = False) -> None:
        self.stack.setCurrentWidget(self.loading)
        self.loading_label.setText(text)
        self.loading_bar.setVisible(busy)
        self.retry.setVisible(retry)

    def _local_path(self, key: str) -> str | None:
        path = (self._current_book().get("local") or {}).get(key)
        return path if path and Path(path).exists() else None

    def _with_file(self, key: str, name: str, ready) -> None:
        """Use the downloaded copy if there is one, otherwise a temporary copy."""
        local = self._local_path(key)
        if local:
            ready(local)
            return
        self._status("Opening book…")
        self.loading_bar.setRange(0, 0)
        result = self.ctx.streams.fetch(self.book["id"], name)
        if isinstance(result, Path):
            ready(str(result))
            return
        self.pending = result

        def advanced(got: int, total: int) -> None:
            if total > 0:
                self.loading_bar.setRange(0, 1000)
                self.loading_bar.setValue(int(got / total * 1000))

        def done(path: str) -> None:
            self.pending = None
            ready(path)

        def failed(message: str) -> None:
            self.pending = None
            self._status(message, busy=False, retry=True)

        result.progress.connect(advanced)
        result.done.connect(done)
        result.failed.connect(failed)

    def open_view(self, name: str | None) -> None:
        self.wanted_view = name
        if self.pending:
            self.pending.cancel()
            self.pending = None
        if name == "text":
            self._open_text()
        elif name == "scan":
            self._open_scan()
        else:
            self._status("This book has nothing that can be displayed.", busy=False)

    def _open_text(self) -> None:
        epub, text = self.plan.get("epub"), self.plan.get("text")
        if epub:
            self._with_file("epub", epub["name"], lambda p: self._text_ready(p, "epub"))
        elif text:
            self._with_file("text", text["name"], lambda p: self._text_ready(p, text.get("form", "plain")))
        else:
            self._status("This book has no text version.", busy=False)

    def _text_ready(self, path: str, form: str) -> None:
        try:
            if form == "epub":
                self.text_book = EpubBook(path, self.token)
            else:
                self.text_book = TextBook(path, self.book["title"], self.token, ocr=form == "djvu")
        except (BookError, OSError) as error:
            self.text_book = None
            # A bad text copy shouldn't strand the reader if scans exist.
            if self.plan.get("pages") or self.plan.get("pdf"):
                self.ctx.toast.emit("The text copy was damaged, so showing the scans instead.")
                self.open_view("scan")
            else:
                self._status(str(error), busy=False)
            return
        self.engine.handler.books[self.token] = self.text_book
        self._fill_toc()
        self.text_view = TextView(self.engine)
        self.text_view.set_background(READER_THEMES[self.settings["theme"]]["bg"])
        self.text_view.message.connect(self._js_message)
        self.text_view.ready.connect(self._text_view_ready)
        self.stack.addWidget(self.text_view)

    def _text_view_ready(self) -> None:
        assert self.text_book and self.text_view
        self.text_view.call("applySettings", self._settings_payload())
        self.stack.setCurrentWidget(self.text_view)
        self._set_view("text")
        saved = self._current_book().get("position", {}).get("text")
        if saved:
            spine = min(int(saved.get("spine", 0)), self.text_book.length - 1)
            self._send_chapter(spine, {"offset": int(saved.get("offset", 0))})
        elif self.progress_value > 0.001:
            spine, part = self.text_book.locate(self.progress_value)
            self._send_chapter(spine, {"fraction": part})
        else:
            self._send_chapter(0, {"at": "start"})
        self.text_view.setFocus()

    def _chapter(self, spine: int) -> Chapter | None:
        book = self.text_book
        if not book or not 0 <= spine < book.length:
            return None
        if spine not in self.chapters:
            self.chapters[spine] = book.chapter(spine)
            while len(self.chapters) > CHAPTERS_KEPT:
                del self.chapters[max(self.chapters, key=lambda c: abs(c - self.spine))]
        return self.chapters[spine]

    def _prepare_neighbors(self) -> None:
        """Have the next and previous chapters ready before they're asked for."""
        for spine in (self.spine + 1, self.spine - 1):
            try:
                self._chapter(spine)
            except Exception:  # Reported properly if the reader actually gets there.
                pass

    def _send_chapter(self, spine: int, target: dict, turn: str = "") -> None:
        if not self.text_book or not self.text_view or not 0 <= spine < self.text_book.length:
            return
        try:
            chapter = self._chapter(spine)
            html, css = chapter.html, chapter.css
        except Exception as error:  # A single damaged chapter must not close the book.
            html, css = f"<p>This chapter couldn't be displayed.</p><p>{type(error).__name__}</p>", ""
        self.spine = spine
        self.text_view.call("render", {
            "html": html, "css": css, "spine": spine, "target": target, "turn": turn,
            "highlights": self._highlights_for(spine),
        })
        QTimer.singleShot(150, self, self._prepare_neighbors)

    def _highlights_for(self, spine: int) -> list[dict]:
        return [
            {"id": h["id"], "start": h["start"], "end": h["end"], "color": h["color"], "note": h["note"], "text": h["text"]}
            for h in self.ctx.library.highlights(self.book["id"], spine)
        ]

    def _open_scan(self) -> None:
        local_pdf = self._local_path("pdf")
        pages = int(self.plan.get("pages") or 0)
        if local_pdf:
            self._scan_ready(lambda: PdfPages(local_pdf, self))
        elif pages:
            self._scan_ready(lambda: RemotePages(self.ctx.net, self.book["id"], pages, stream_width(), self))
        elif self.plan.get("pdf"):
            self._with_file("pdf", self.plan["pdf"]["name"],
                            lambda p: self._scan_ready(lambda: PdfPages(p, self)))
        else:
            self._status("This book has no page scans.", busy=False)

    def _scan_ready(self, make_provider) -> None:
        try:
            self.provider = make_provider()
        except BookError as error:
            self._status(str(error), busy=False)
            return
        self.scan_view = ScanView()
        self.scan_view.animate = self.settings["animate"]
        self.scan_view.page_changed.connect(self._scan_moved)
        self.scan_view.zoom_changed.connect(self._sync_zoom)
        if isinstance(self.provider, RemotePages):
            self.provider.progress.connect(self._stream_progress)
        palette = READER_THEMES[self.settings["theme"]]
        self.scan_view.set_colors(palette["bg"], palette["line"], palette["muted"])
        self.stack.addWidget(self.scan_view)
        saved = self._current_book().get("position", {}).get("scan")
        index = int(saved.get("page", 0)) if saved else int(self.progress_value * self.provider.total)
        self.stack.setCurrentWidget(self.scan_view)
        self._set_view("scan")
        self.scan_view.set_provider(self.provider, index)
        self.scan_view.setFocus()

    def _set_view(self, name: str) -> None:
        self.view_name = name
        self.ctx.library.set_view(self.book["id"], name)
        self.contents_button.setVisible(name == "text" and self.toc_list.count() > 0)
        self._sync_zoom()

    # Moving around -------------------------------------------------------------
    def next_page(self) -> None:
        self._touch()
        if self.view_name == "text" and self.text_view:
            self.text_view.call("next")
        elif self.scan_view:
            self.scan_view.next()

    def prev_page(self) -> None:
        self._touch()
        if self.view_name == "text" and self.text_view:
            self.text_view.call("prev")
        elif self.scan_view:
            self.scan_view.previous()

    def _slider_released(self) -> None:
        self.slider_active = False
        fraction = self.slider.value() / 1000
        if self.view_name == "text" and self.text_book:
            spine, part = self.text_book.locate(fraction)
            self._send_chapter(spine, {"fraction": part})
        elif self.scan_view:
            self.scan_view.go(int(fraction * self.scan_view.total))

    def _js_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "position":
            self._text_moved(message)
        elif kind == "need_chapter":
            self._touch()
            at = message.get("at", "start")
            self._send_chapter(int(message["spine"]), {"at": at}, "next" if at == "start" else "prev")
        elif kind == "link":
            self._touch()
            spine = int(message["spine"])
            if spine == self.spine and message.get("fragment"):
                self.text_view.page().runJavaScript(
                    f"OS.goTo({{fragment:{json.dumps(message['fragment'])}}})")
            else:
                self._send_chapter(spine, {"fragment": message.get("fragment", "")})
        elif kind == "external":
            self._open_external(str(message.get("url", "")))
        elif kind == "copy":
            QGuiApplication.clipboard().setText(str(message.get("text", "")))
        elif kind == "hl_add":
            self._add_highlight(message)
        elif kind == "hl_color":
            self.ctx.library.update_highlight(int(message["id"]), color=str(message["color"]))
            self._highlights_changed()
        elif kind == "hl_note":
            self.focus_note("highlight", int(message["id"]))
        elif kind == "hl_delete":
            self.ctx.library.delete_highlight(int(message["id"]))
            self._highlights_changed()

    def _open_external(self, url: str) -> None:
        link = QUrl(url)
        if link.scheme() not in ("http", "https"):
            return
        answer = QMessageBox.question(self, "Open Link", f"Open this link in your web browser?\n\n{url}")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(link)

    def _text_moved(self, message: dict) -> None:
        self._touch()
        book = self.text_book
        if not book:
            return
        self.spine = int(message["spine"])
        self.offset = int(message["offset"])
        self.page_start = int(message["first"])
        self.end_offset = int(message["end"])
        self.progress_value = book.fraction(self.spine, float(message["fraction"]))
        parts = [f"Page {book.page_at(self.spine, self.page_start)} of {book.page_count()}"]
        title = book.title_of(self.spine)
        if title:
            parts.insert(0, title if len(title) <= 34 else title[:32].rstrip() + "…")
        self.position_label.setText(" · ".join(parts))
        self._position_done()

    def _scan_moved(self, index: int, total: int) -> None:
        self._touch()
        self.scan_page = index
        self.progress_value = (index + 1) / total if total else 0
        self.position_label.setText(f"Page {index + 1} of {total}")
        self._position_done()

    def _position_done(self) -> None:
        if not self.slider_active:
            self.slider.setValue(int(self.progress_value * 1000))
        self._sync_bookmark()
        self.save_timer.start()

    def _save(self) -> None:
        if self.view_name == "text":
            position = {"spine": self.spine, "offset": self.offset}
        elif self.view_name == "scan":
            position = {"page": self.scan_page}
        else:
            return
        self.ctx.library.save_position(self.book["id"], self.view_name, position, self.progress_value)

    # Side panel ----------------------------------------------------------------
    def _panel(self, index: int, force: bool = False) -> None:
        if self.panel.isVisible() and self.panel_stack.currentIndex() == index and not force:
            self._hide_panel()
            return
        self.panel_stack.setCurrentIndex(index)
        self.panel_title.setText(("Contents", "Search", "Notes")[index])
        self.panel.show()
        for position, button in enumerate((self.contents_button, self.search_button, self.notes_button)):
            button.setChecked(position == index)
        if index == 1:
            self.search_box.setFocus()
            self.search_box.selectAll()
        elif index == 2:
            self._fill_notes()

    def _hide_panel(self) -> None:
        self._flush_notes()
        self.panel.hide()
        for button in (self.contents_button, self.search_button, self.notes_button):
            button.setChecked(False)
        (self.text_view or self.scan_view or self).setFocus()

    def _fill_toc(self) -> None:
        self.toc_list.clear()
        for entry in (self.text_book.toc if self.text_book else []):
            item = QListWidgetItem("    " * min(entry.depth, 3) + entry.title)
            item.setData(Qt.ItemDataRole.UserRole, (entry.spine, entry.fragment))
            self.toc_list.addItem(item)

    def _toc_chosen(self, item: QListWidgetItem) -> None:
        spine, fragment = item.data(Qt.ItemDataRole.UserRole)
        self._send_chapter(spine, {"fragment": fragment} if fragment else {"at": "start"})

    def _search(self) -> None:
        query = self.search_box.text().strip()
        self.search_list.clear()
        self.search_results = []
        if not query:
            self.search_status.setText("")
            return
        if self.view_name == "text" and self.text_book:
            self.search_status.setText("Searching…")
            QApplication.processEvents()
            hits = self.text_book.search(query)
            self.search_results = [
                {"view": "text", "spine": h.spine, "offset": h.offset, "query": query} for h in hits]
            for hit in hits:
                self._add_hit(f"{hit.chapter}\n{hit.snippet}" if hit.chapter else hit.snippet)
            self._search_done()
        elif self.view_name == "scan" and self.scan_view:
            if isinstance(self.provider, PdfPages):
                self._search_pages(query, [self.provider.page_text(i) for i in range(self.provider.total)])
            else:
                self._search_ocr(query)

    def _search_ocr(self, query: str) -> None:
        if self.ocr_pages is not None:
            self._search_pages(query, self.ocr_pages)
            return
        name = self.plan.get("ocr_xml")
        if not name:
            self.search_status.setText("This scan has no searchable text.")
            return
        self.search_status.setText("Getting searchable text…")

        def ready(path: str | Path) -> None:
            try:
                self.ocr_pages = parse_ocr_pages(Path(path).read_text("utf-8", "replace"))
            except OSError:
                self.search_status.setText("Couldn't read the searchable text.")
                return
            self._search_pages(query, self.ocr_pages)

        result = self.ctx.streams.fetch(self.book["id"], name)
        if isinstance(result, Path):
            ready(result)
        else:
            result.done.connect(ready)
            result.failed.connect(self.search_status.setText)

    def _search_pages(self, query: str, pages: list[str]) -> None:
        needle = query.lower()
        for index, text in enumerate(pages):
            position = text.lower().find(needle)
            if position < 0:
                continue
            snippet = text[max(0, position - 50): position + len(needle) + 70].strip()
            self.search_results.append({"view": "scan", "page": index})
            self._add_hit(f"Page {index + 1}\n…{snippet}…")
            if len(self.search_results) >= 300:
                break
        self._search_done()

    def _add_hit(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, self.search_list.count())
        self.search_list.addItem(item)

    def _search_done(self) -> None:
        count = len(self.search_results)
        self.search_status.setText("No matches" if not count else f"{count}{'+' if count >= 300 else ''} match{'es' if count != 1 else ''}")

    def _hit_chosen(self, item: QListWidgetItem) -> None:
        hit = self.search_results[item.data(Qt.ItemDataRole.UserRole)]
        if hit["view"] == "text":
            self._send_chapter(hit["spine"], {"find": hit["query"], "offset": hit["offset"]})
        elif self.scan_view:
            self.scan_view.go(hit["page"])

    # Highlights, bookmarks and notes --------------------------------------------
    def _add_highlight(self, message: dict) -> None:
        highlight_id = self.ctx.library.add_highlight(
            self.book["id"], int(message["spine"]), int(message["start"]), int(message["end"]),
            str(message["text"]), str(message.get("color", "yellow")))
        self._highlights_changed()
        if message.get("note"):
            self.focus_note("highlight", highlight_id)

    def _highlights_changed(self, refill: bool = True) -> None:
        if self.text_view and self.view_name == "text":
            self.text_view.call("setHighlights", self._highlights_for(self.spine))
        if refill and self.panel.isVisible() and self.panel_stack.currentIndex() == 2:
            self._fill_notes()

    def focus_note(self, kind: str, record_id: int) -> None:
        """Open the notes panel on one card, ready to type."""
        self._panel(2, force=True)
        card = next((c for c in self.note_cards if c.kind == kind and c.record["id"] == record_id), None)
        if card:
            card.editor.setFocus()
            QTimer.singleShot(0, card, lambda: self.notes_scroll.ensureWidgetVisible(card, 0, 12))

    def _note_edited(self, card: _NoteCard) -> None:
        if card.kind == "highlight":
            self.ctx.library.update_highlight(card.record["id"], note=card.record["note"])
            self._highlights_changed(refill=False)
        else:
            self.ctx.library.update_bookmark(card.record["id"], card.record["note"])

    def _flush_notes(self) -> None:
        for card in self.note_cards:
            card.save()

    def _go_to_note(self, card: _NoteCard) -> None:
        record = card.record
        if card.kind == "highlight":
            self._send_chapter(record["spine"], {"offset": record["start"]})
        elif record["view"] == "text":
            self._send_chapter(record["spine"], {"offset": record["offset"]})
        elif self.scan_view:
            self.scan_view.go(record["page"])

    def _remove_note(self, card: _NoteCard) -> None:
        if card.kind == "highlight":
            self.ctx.library.delete_highlight(card.record["id"])
            self._highlights_changed()
        else:
            self.ctx.library.delete_bookmark(card.record["id"])
            self._sync_bookmark()
            self._fill_notes()

    def _current_bookmark(self) -> dict | None:
        for mark in self.ctx.library.bookmarks(self.book["id"]):
            if mark["view"] != self.view_name:
                continue
            if self.view_name == "scan" and mark["page"] in (self.scan_view._visible() if self.scan_view else []):
                return mark
            if self.view_name == "text" and mark["spine"] == self.spine and (
                    self.page_start <= mark["offset"] < max(self.end_offset, self.page_start + 1)):
                return mark
        return None

    def _sync_bookmark(self) -> None:
        self.bookmark_button.setChecked(self._current_bookmark() is not None)

    def _toggle_bookmark(self) -> None:
        existing = self._current_bookmark()
        if existing:
            self.ctx.library.delete_bookmark(existing["id"])
        elif self.view_name == "text" and self.text_book:
            label = self.text_book.title_of(self.spine) or f"Section {self.spine + 1}"
            self.ctx.library.add_bookmark(self.book["id"], "text", self.spine, self.offset, 0, label)
        elif self.view_name == "scan":
            self.ctx.library.add_bookmark(self.book["id"], "scan", 0, 0, self.scan_page, f"Page {self.scan_page + 1}")
        self._sync_bookmark()
        if self.panel.isVisible() and self.panel_stack.currentIndex() == 2:
            self._fill_notes()

    def _location(self, spine: int, offset: int) -> str:
        book = self.text_book
        if not book:
            return "Earlier text view"
        try:
            parts = [f"Page {book.page_at(spine, offset)}"]
        except Exception:
            return book.title_of(spine) or "In this book"
        title = book.title_of(spine)
        if title:
            parts.append(title if len(title) <= 30 else title[:28].rstrip() + "…")
        return " · ".join(parts)

    def _fill_notes(self) -> None:
        self._flush_notes()
        for card in self.note_cards:
            self.notes_layout.removeWidget(card)
            card.deleteLater()
        self.note_cards = []
        entries: list[tuple[tuple, str, dict, str, bool]] = []
        for mark in self.ctx.library.bookmarks(self.book["id"]):
            record = dict(mark)
            if record["view"] == "scan":
                entries.append(((0, record["page"], 0), "bookmark", record, f"Page {record['page'] + 1}", self.view_name == "scan"))
            elif self.view_name == "text":
                entries.append(((0, record["spine"], record["offset"]), "bookmark", record,
                                self._location(record["spine"], record["offset"]), True))
            else:
                entries.append(((1, record["spine"], record["offset"]), "bookmark", record, "Earlier text view", False))
        for highlight in self.ctx.library.highlights(self.book["id"]):
            record = dict(highlight)
            usable = self.view_name == "text"
            where = self._location(record["spine"], record["start"]) if usable else "Earlier text view"
            entries.append((((0 if usable else 1), record["spine"], record["start"]), "highlight", record, where, usable))
        entries.sort(key=lambda e: e[0])
        for position, (_key, kind, record, where, usable) in enumerate(entries):
            card = _NoteCard(self, kind, record, where, usable)
            self.notes_layout.insertWidget(position + 1, card)
            self.note_cards.append(card)
        self.notes_empty.setVisible(not entries)
        self.notes_empty.setText(
            "Nothing here yet. Select text to highlight it, or use the bookmark button to mark a page."
            if self.view_name == "text" else "Nothing here yet. Use the bookmark button to mark a page and add a note.")

    # Reading time and shutdown --------------------------------------------------
    def _touch(self) -> None:
        self.last_active = time.monotonic()

    def _tick(self) -> None:
        if self.isVisible() and self.isActiveWindow() and time.monotonic() - self.last_active < IDLE_SECONDS:
            self.ctx.library.add_activity(self.book["id"], TICK_SECONDS)

    def leave(self) -> None:
        self.closed.emit()

    def shutdown(self) -> None:
        if self.shut_down:
            return
        self.shut_down = True
        self._flush_notes()
        self._save()
        self.save_timer.stop()
        self.tick.stop()
        if self.pending:
            self.pending.cancel()
        if self.provider:
            self.provider.stop()
        self.engine.handler.books.pop(self.token, None)
        if self.text_view:
            self.text_view.shutdown()
        if self.text_book:
            self.text_book.close()
