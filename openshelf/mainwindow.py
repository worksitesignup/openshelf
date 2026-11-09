"""The main window: sidebar navigation, page stack, player bar and notifications."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, catalog
from .context import AppContext
from .detail import DetailPage
from .pages import BrowsePage, DiscoverPage, GoalsPage, LibraryPage, SearchPage, SettingsPage
from .player import AudioPlayer, PlayerBar
from .reader import ReaderPage
from .textview import ReaderEngine
from .theme import theme
from .widgets import label, tool_button


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 840)
        self.setMinimumSize(760, 520)
        self.setAcceptDrops(True)
        self.engine = ReaderEngine(self)
        self.reader: ReaderPage | None = None
        self.history: list[tuple] = []
        self.current: tuple = ("home",)
        ctx.player = AudioPlayer(ctx)

        self.home = DiscoverPage(ctx, "Home", "text", catalog.TEXT_SHELVES)
        self.audio = DiscoverPage(ctx, "Audiobooks", "audio", catalog.AUDIO_SHELVES)
        self.browse = BrowsePage(ctx)
        self.search = SearchPage(ctx)
        self.library = LibraryPage(ctx)
        self.goals = GoalsPage(ctx)
        self.settings_page = SettingsPage(ctx)
        self.detail = DetailPage(ctx)

        self.library_root = QWidget()
        layout = QHBoxLayout(self.library_root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.back = tool_button("back", "Back (Alt+Left)")
        self.back.clicked.connect(self.go_back)
        self.back_row = QWidget()
        back_layout = QHBoxLayout(self.back_row)
        back_layout.setContentsMargins(14, 8, 14, 0)
        back_layout.addWidget(self.back)
        back_layout.addStretch(1)
        self.back_row.hide()
        right.addWidget(self.back_row)
        self.stack = QStackedWidget()
        for page in (self.home, self.browse, self.audio, self.search, self.library, self.goals, self.settings_page, self.detail):
            self.stack.addWidget(page)
        right.addWidget(self.stack, 1)
        layout.addLayout(right, 1)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.modes = QStackedWidget()  # Browsing, or the book being read.
        self.modes.addWidget(self.library_root)
        outer.addWidget(self.modes, 1)
        self.player_bar = PlayerBar(ctx, ctx.player)
        outer.addWidget(self.player_bar)
        self.setCentralWidget(root)

        self.toast = QLabel(self)
        self.toast.setStyleSheet(
            "background:#2C2C2F;color:#F2F2F3;border-radius:9px;padding:9px 15px;")
        self.toast.hide()
        self.toast_timer = QTimer(self, singleShot=True, interval=3200, timeout=self.toast.hide)

        ctx.open_book.connect(self.open_book)
        ctx.show_detail.connect(self.show_detail)
        ctx.show_search.connect(self.show_fixed_search)
        ctx.search_text.connect(self.show_text_search)
        ctx.browse_requested.connect(lambda: self.navigate(("browse",)))
        ctx.show_shelf.connect(self.show_shelf)
        ctx.toast.connect(self.show_toast)
        ctx.downloads.finished.connect(self._download_finished)
        ctx.downloads.failed.connect(self._download_failed)
        ctx.player.failed.connect(self.show_toast)
        ctx.library.collections_changed.connect(self._fill_collections)
        theme.changed.connect(self._repaint_sidebar_icons)
        # Owned by the browsing screen so they don't compete with the reader's own keys.
        QShortcut(QKeySequence("Alt+Left"), self.library_root, activated=self.go_back)
        QShortcut(QKeySequence("Ctrl+F"), self.library_root, activated=lambda: self.navigate(("search",)))
        self.navigate(("home",), remember=False)

    # Sidebar --------------------------------------------------------------------
    def _side_button(self, text: str, icon: str, route: tuple) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("SideItem")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(18, 18))
        button.setIcon(theme.icon(icon, size=18))
        button.setProperty("icon_name", icon)
        button.clicked.connect(lambda: self._side_clicked(route))
        self.side_group.addButton(button)
        self.route_buttons[route] = button
        return button

    def _side_clicked(self, route: tuple) -> None:
        if route == ("search",) and self.search.scope_query:
            self.search.reset()  # Coming from the sidebar means a fresh search, not the last category.
        self.navigate(route)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(224)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(10, 16, 10, 10)
        outer.setSpacing(1)
        self.side_group = QButtonGroup(self)
        self.side_group.setExclusive(True)
        self.route_buttons: dict[tuple, QPushButton] = {}
        outer.addWidget(label(APP_NAME, "Brand"))
        for text, icon, route in (
            ("Home", "home", ("home",)), ("Browse", "compass", ("browse",)),
            ("Audiobooks", "headphones", ("audio",)), ("Search", "search", ("search",)),
        ):
            outer.addWidget(self._side_button(text, icon, route))
        outer.addWidget(label("LIBRARY", "SideHeader"))
        for text, icon, shelf in (
            ("My Library", "library", "library"), ("Want to Read", "want", "want"), ("Finished", "check", "finished"),
        ):
            outer.addWidget(self._side_button(text, icon, ("shelf", shelf, None)))
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(label("COLLECTIONS", "SideHeader"), 1)
        add = tool_button("plus", "New collection", 14)
        add.clicked.connect(self._new_collection)
        heading.addWidget(add)
        outer.addLayout(heading)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        self.collection_layout = QVBoxLayout(holder)
        self.collection_layout.setContentsMargins(0, 0, 0, 0)
        self.collection_layout.setSpacing(1)
        self.collection_layout.addStretch(1)
        scroller.setWidget(holder)
        outer.addWidget(scroller, 1)
        outer.addWidget(self._side_button("Reading Goals", "target", ("goals",)))
        outer.addWidget(self._side_button("Settings", "gear", ("settings",)))
        self.collection_buttons: list[QPushButton] = []
        self._fill_collections()
        return sidebar

    def _fill_collections(self) -> None:
        for button in self.collection_buttons:
            self.side_group.removeButton(button)
            self.route_buttons.pop(("shelf", "collection", button.property("collection_id")), None)
            button.deleteLater()
        self.collection_buttons = []
        for index, collection in enumerate(self.ctx.library.collections()):
            route = ("shelf", "collection", collection["id"])
            button = self._side_button(collection["name"], "folder", route)
            button.setProperty("collection_id", collection["id"])
            self.collection_layout.insertWidget(index, button)
            self.collection_buttons.append(button)
        self._mark_route()

    def _repaint_sidebar_icons(self) -> None:
        for button in self.side_group.buttons():
            button.setIcon(theme.icon(button.property("icon_name"), size=18))

    def _new_collection(self) -> None:
        name, ok = QInputDialog.getText(self, "New Collection", "Name:")
        if ok and name.strip():
            collection_id = self.ctx.library.create_collection(name)
            self.navigate(("shelf", "collection", collection_id))

    # Navigation -----------------------------------------------------------------
    def navigate(self, route: tuple, remember: bool = True) -> None:
        if remember and route != self.current:
            self.history.append(self.current)
            del self.history[:-40]
        self.current = route
        kind = route[0]
        # Keep focus off the shelves: when a focused cover is replaced, Qt moves focus to
        # the next widget and scrolls to it, which makes the page jump while it loads.
        self.stack.setFocus()
        if kind == "home":
            self.stack.setCurrentWidget(self.home)
        elif kind == "browse":
            self.stack.setCurrentWidget(self.browse)
        elif kind == "audio":
            self.stack.setCurrentWidget(self.audio)
        elif kind == "search":
            self.stack.setCurrentWidget(self.search)
            self.search.box.setFocus()
        elif kind == "shelf":
            self.library.show_shelf(route[1], route[2])
            self.stack.setCurrentWidget(self.library)
        elif kind == "goals":
            self.stack.setCurrentWidget(self.goals)
        elif kind == "settings":
            self.stack.setCurrentWidget(self.settings_page)
        elif kind == "detail":
            self.detail.show_book(route[1])
            self.stack.setCurrentWidget(self.detail)
        self.back_row.setVisible(kind == "detail" or (kind == "search" and bool(self.history)))
        self._mark_route()

    def _mark_route(self) -> None:
        button = None if self.current[0] == "detail" else self.route_buttons.get(self.current)
        if button:
            button.setChecked(True)
        else:
            self.side_group.setExclusive(False)
            for item in self.side_group.buttons():
                item.setChecked(False)
            self.side_group.setExclusive(True)

    def go_back(self) -> None:
        if self.history:
            self.navigate(self.history.pop(), remember=False)

    def show_detail(self, book: dict) -> None:
        self.navigate(("detail", book))

    def show_fixed_search(self, title: str, query: str, sort: str) -> None:
        self.navigate(("search",))
        self.search.show_fixed(title, query, sort)
        self.search.grid.setFocus()

    def show_text_search(self, text: str) -> None:
        self.navigate(("search",))
        self.search.search_for(text)

    def show_shelf(self, shelf: str, collection) -> None:
        self.navigate(("shelf", shelf, collection))

    def show_toast(self, text: str) -> None:
        self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 90)
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start()

    # Books ----------------------------------------------------------------------
    def open_book(self, book: dict) -> None:
        if book.get("kind") == "audio":
            self.ctx.player.resume_book(book)
            return
        if self.reader and self.reader.book["id"] == book["id"]:
            self.modes.setCurrentWidget(self.reader)
            return
        if not (book.get("plan") or {}).get("available"):
            QMessageBox.information(self, APP_NAME, "This book can't be opened.")
            return
        self.close_reader()
        self.reader = ReaderPage(self.ctx, self.engine, book)
        self.reader.closed.connect(self.close_reader)
        self.modes.addWidget(self.reader)
        self.modes.setCurrentWidget(self.reader)
        self.setWindowTitle(f"{book['title']} – {APP_NAME}")

    def close_reader(self) -> None:
        """Save the reader's place and go back to whatever was on screen before."""
        reader, self.reader = self.reader, None
        if reader is None:
            return
        reader.shutdown()
        self.modes.setCurrentWidget(self.library_root)
        self.modes.removeWidget(reader)
        reader.deleteLater()
        self.setWindowTitle(APP_NAME)
        if self.isFullScreen():
            self.showNormal()
        if self.current[0] == "detail":
            self.detail.refresh_state()
        self.stack.setFocus()

    def _download_finished(self, book_id: str) -> None:
        book = self.ctx.library.book(book_id)
        if book:
            self.show_toast(f"“{book['title']}” is now in your library")

    def _download_failed(self, _book_id: str, message: str) -> None:
        if message:
            self.show_toast(message)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and all(u.toLocalFile().lower().endswith((".epub", ".pdf")) for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.library.import_paths([u.toLocalFile() for u in event.mimeData().urls()])

    def closeEvent(self, event) -> None:
        self.ctx.player.save()
        self.close_reader()
        super().closeEvent(event)


__all__ = ["MainWindow", "QAction"]
