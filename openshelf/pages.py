"""The main screens: Home, Audiobooks, Search, Library, Goals and Settings."""
from __future__ import annotations

import shutil
from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLineEdit,
    QMenu, QMessageBox, QProgressBar, QPushButton, QToolTip, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, catalog, paths
from .bookfile import BookError
from .context import AppContext
from .theme import format_bytes, theme
from .widgets import CountUp, CoverCard, EmptyState, FlowGrid, Page, Shelf, debounce, label, tool_button


class DiscoverPage(Page):
    """Shelf-based browsing. Home shows books; Audiobooks shows audio."""

    def __init__(self, ctx: AppContext, heading: str, kind: str, shelves: tuple) -> None:
        super().__init__()
        self.ctx = ctx
        self.kind = kind
        self.shelf_defs = shelves
        self.started = False
        self.failures = 0
        if kind == "text":
            self.body.addWidget(self._hero())
        else:
            self.body.addWidget(label(heading, "H1"))

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(16, 12, 12, 12)
        self.banner_text = label("Can't reach the Internet Archive. Your library still works.", "Muted", wrap=True)
        row.addWidget(self.banner_text, 1)
        retry = QPushButton("Try Again")
        retry.clicked.connect(self.reload)
        row.addWidget(retry)
        self.banner.hide()
        self.body.addWidget(self.banner)

        self.continue_shelf = Shelf(ctx, "Continue Listening" if kind == "audio" else "Continue Reading",
                                    show_progress=True)
        self.continue_shelf.hide()
        self.body.addWidget(self.continue_shelf)

        self.recommendations = QVBoxLayout()
        self.recommendations.setSpacing(22)
        self.body.addLayout(self.recommendations)
        self.rec_seeds: list[str] = []

        self.shelves: list[tuple[Shelf, str, str]] = []
        for title, fragment, sort in shelves:
            query = catalog.shelf_query(fragment, kind)
            shelf = Shelf(ctx, title, see_all=self._see_all(title, query, sort))
            shelf.loaded.connect(self._shelf_loaded)
            self.body.addWidget(shelf)
            self.shelves.append((shelf, query, sort))
        self.body.addStretch(1)
        self.refresh_timer = debounce(self, 400, self.refresh_personal)
        ctx.library.library_changed.connect(self.refresh_timer.start)
        ctx.content_filter_changed.connect(self._filter_changed)

    def _filter_changed(self) -> None:
        self.started = False  # Shelves reload next time this page is shown.
        self.rec_seeds = []

    def _hero(self) -> QWidget:
        """Opens with the real size of the catalog, counted live."""
        hero = QWidget()
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(4)
        self.hero_number = CountUp("Millions", "HeroNumber")
        layout.addWidget(self.hero_number)
        layout.addWidget(label("of free books, magazines and documents. All of them are yours to read.", "Lead", wrap=True))
        browse = QPushButton("Browse the Whole Catalog")
        browse.setObjectName("Primary")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self.ctx.browse_requested.emit)
        layout.addSpacing(8)
        layout.addWidget(browse, 0, Qt.AlignmentFlag.AlignLeft)
        self.hero_number.set_value(self.ctx.counts.get(catalog.ALL_TEXTS) or 0)
        return hero

    def _see_all(self, title: str, query: str, sort: str):
        return lambda: self.ctx.show_search.emit(title, query, sort)

    def _shelf_loaded(self, ok: bool) -> None:
        if not ok:
            self.failures += 1
            if self.failures >= 2:
                self.banner.show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_personal()
        if not self.started:
            self.started = True
            self.reload()

    def reload(self) -> None:
        self.banner.hide()
        self.failures = 0
        for index, (shelf, query, sort) in enumerate(self.shelves):
            shelf.show()
            shelf.show_skeleton()
            # Staggered so a screenful of shelves doesn't hammer a nonprofit's servers.
            QTimer.singleShot(120 * index, shelf, lambda s=shelf, q=query, o=sort: s.load(q, o))

    def refresh_personal(self) -> None:
        library = self.ctx.library
        reading = [b for b in library.books("reading") if b["kind"] == self.kind][:16]
        self.continue_shelf.set_books(reading)
        self.continue_shelf.setVisible(bool(reading))
        self._refresh_recommendations()

    def _refresh_recommendations(self) -> None:
        library = self.ctx.library
        everything = [b for b in library.books("all") if b["kind"] == self.kind]
        seeds = catalog.recommendation_seeds(
            [b for b in everything if b["last_opened"] or b["want"] or b["finished_at"]], limit=2)
        ids = [s["id"] for s in seeds]
        if ids == self.rec_seeds:
            return
        self.rec_seeds = ids
        while self.recommendations.count():
            widget = self.recommendations.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        owned = {b["id"] for b in everything}
        for seed in seeds:
            fragment = catalog.recommendation_fragment(seed, everything)
            if not fragment:
                continue
            title = seed["title"] if len(seed["title"]) < 48 else seed["title"][:45].rstrip() + "…"
            shelf = Shelf(self.ctx, f"Because You Read {title}" if self.kind == "text"
                          else f"Because You Listened to {title}")
            self.recommendations.addWidget(shelf)
            shelf.load(catalog.shelf_query(fragment, self.kind), rows=20, exclude=owned)


class CategoryTile(QPushButton):
    """A way into the catalog, showing how many titles are behind it."""

    def __init__(self, name: str, tip: str = "") -> None:
        super().__init__()
        self.setObjectName("CategoryTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(204, 76)
        self.setAccessibleName(name)
        if tip:
            self.setToolTip(tip)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(1)
        title = label(name, "TileName")
        self.count = label(" ", "Muted")
        for part in (title, self.count):
            part.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(part)

    def set_count(self, count: int | None) -> None:
        if count:
            self.count.setText(f"{count:,}")


class BrowsePage(Page):
    """The whole catalog, laid out by subject, era, language and library."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.tiles: list[tuple[CategoryTile, str]] = []
        self.body.addWidget(label("Browse", "H1"))
        hero = QVBoxLayout()
        hero.setSpacing(2)
        self.number = CountUp("Millions", "HeroNumber")
        hero.addWidget(self.number)
        self.caption = label("of free books, magazines and documents, from the Internet Archive.", "Lead", wrap=True)
        hero.addWidget(self.caption)
        self.body.addLayout(hero)
        self.box = QLineEdit()
        self.box.setObjectName("SearchBox")
        self.box.setPlaceholderText("Search all of it")
        self.box.returnPressed.connect(self._search)
        self.body.addWidget(self.box)
        self.number.set_value(ctx.counts.get(catalog.ALL_TEXTS) or 0)
        for title, entries in catalog.browse_tiles().items():
            self._section(title, entries)
        self.body.addStretch(1)

    def _section(self, title: str, entries: tuple[tuple[str, str, str], ...]) -> None:
        self.body.addWidget(label(title, "H2"))
        grid = FlowGrid(12)
        tiles = []
        for name, query, tip in entries:
            tile = CategoryTile(name, tip)
            tile.set_count(self.ctx.counts.get(query))
            tile.clicked.connect(lambda _=False, n=name, q=query: self.ctx.show_search.emit(n, q, "downloads desc"))
            tiles.append(tile)
            self.tiles.append((tile, query))
        grid.add_items(tiles)
        self.body.addWidget(grid)

    def _search(self) -> None:
        text = self.box.text().strip()
        if text:
            self.box.clear()
            self.ctx.search_text.emit(text)


class SearchPage(Page):
    """Search and category results, a page at a time, with the search controls always in view."""

    PAGE_SIZE = 48
    MAX_PAGE = 10_000 // PAGE_SIZE  # The Archive won't list past the first 10,000 of any view.

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.query = ""
        ctx.content_filter_changed.connect(self._filter_changed)
        self.sort = "downloads desc"
        self.page_number = 1
        self.total = 0
        self.request_id = 0
        self.prepare_timer = QTimer(self, singleShot=True, interval=500, timeout=self._prepare_next_page)
        self.dim_timer = QTimer(self, singleShot=True, interval=150, timeout=lambda: self._set_dimmed(True))
        self.scope_title = ""
        self.scope_query = ""
        self.body.setContentsMargins(36, 16, 36, 30)

        self.header = QWidget(self)
        self.header.setObjectName("PinnedHeader")
        top = QVBoxLayout(self.header)
        top.setContentsMargins(36, 18, 36, 10)
        top.setSpacing(10)
        title_row = QHBoxLayout()
        self.title = label("Search", "H1")
        title_row.addWidget(self.title, 1)
        self.clear_fixed = QPushButton("Search Everything")
        self.clear_fixed.setObjectName("Link")
        self.clear_fixed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_fixed.clicked.connect(self.reset)
        self.clear_fixed.hide()
        title_row.addWidget(self.clear_fixed)
        top.addLayout(title_row)
        self.box = QLineEdit()
        self.box.setObjectName("SearchBox")
        self.box.setPlaceholderText("Titles, authors or subjects")
        self.box.setClearButtonEnabled(True)
        self.box.returnPressed.connect(self.search)
        top.addWidget(self.box)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        self.kind = QComboBox()
        for text, value in (("Books", "text"), ("Audiobooks", "audio"), ("Books & Audiobooks", "any")):
            self.kind.addItem(text, value)
        self.language = QComboBox()
        for text, value in catalog.LANGUAGES:
            self.language.addItem(text, value)
        self.sort_box = QComboBox()
        for text, value in catalog.SORTS:
            self.sort_box.addItem(text, value)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Subject")
        self.subject.setMaximumWidth(170)
        self.year_from = QLineEdit()
        self.year_from.setPlaceholderText("From year")
        self.year_to = QLineEdit()
        self.year_to.setPlaceholderText("To year")
        for edit in (self.year_from, self.year_to):
            edit.setMaximumWidth(88)
            edit.setValidator(QIntValidator(1, 2100, edit))
        for widget in (self.kind, self.language, self.sort_box, self.subject, self.year_from, self.year_to):
            filters.addWidget(widget)
        for combo in (self.kind, self.language, self.sort_box):
            combo.activated.connect(self.search)
        for edit in (self.subject, self.year_from, self.year_to):
            edit.returnPressed.connect(self.search)
        filters.addStretch(1)
        self.filter_row = QWidget()
        self.filter_row.setLayout(filters)
        top.addWidget(self.filter_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.status = label("", "Muted")
        status_row.addWidget(self.status, 1)
        self.pager = QWidget()
        pager = QHBoxLayout(self.pager)
        pager.setContentsMargins(0, 0, 0, 0)
        pager.setSpacing(4)
        self.previous_button = tool_button("back", "Previous page", 16)
        self.previous_button.clicked.connect(lambda: self.go_to_page(self.page_number - 1))
        pager.addWidget(self.previous_button)
        pager.addWidget(label("Page", "Muted"))
        self.page_box = QLineEdit("1")
        self.page_box.setFixedWidth(52)
        self.page_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_box.setAccessibleName("Page number")
        self.page_box.setValidator(QIntValidator(1, self.MAX_PAGE, self.page_box))
        self.page_box.returnPressed.connect(lambda: self.go_to_page(int(self.page_box.text() or 1)))
        pager.addWidget(self.page_box)
        self.page_total = label("", "Muted")
        pager.addWidget(self.page_total)
        self.next_button = tool_button("forward", "Next page", 16)
        self.next_button.clicked.connect(lambda: self.go_to_page(self.page_number + 1))
        pager.addWidget(self.next_button)
        self.pager.hide()
        status_row.addWidget(self.pager)
        top.addLayout(status_row)

        self.empty = EmptyState("Search the Internet Archive", "Every book you find here is free to read.")
        self.body.addWidget(self.empty)
        self.grid = FlowGrid()
        self.grid_dim = QGraphicsOpacityEffect(self.grid, opacity=0.4, enabled=False)
        self.grid.setGraphicsEffect(self.grid_dim)
        self.body.addWidget(self.grid)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.bottom_previous = QPushButton("Previous Page")
        self.bottom_previous.clicked.connect(lambda: self.go_to_page(self.page_number - 1))
        self.bottom_next = QPushButton("Next Page")
        self.bottom_next.clicked.connect(lambda: self.go_to_page(self.page_number + 1))
        bottom.addWidget(self.bottom_previous)
        bottom.addWidget(self.bottom_next)
        bottom.addStretch(1)
        self.body.addLayout(bottom)
        self.limit_note = label(
            "The Archive lists the first 10,000 titles of any one view. "
            "Change the sort or add a filter to reach the others.", "Faint", wrap=True)
        self.limit_note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.limit_note.hide()
        self.body.addWidget(self.limit_note)
        self.body.addStretch(1)
        self.bottom_previous.hide()
        self.bottom_next.hide()

    def _place_header(self) -> None:
        height = self.header.sizeHint().height()
        self.setViewportMargins(0, height, 0, 0)
        self.header.setGeometry(0, 0, self.width(), height)
        self.header.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_header()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_header()

    def _header_changed(self) -> None:
        QTimer.singleShot(0, self, self._place_header)

    def _clear_filters(self) -> None:
        self.language.setCurrentIndex(0)
        self.subject.clear()
        self.year_from.clear()
        self.year_to.clear()

    def _clear_results(self) -> None:
        self.request_id += 1
        self.prepare_timer.stop()
        self._set_dimmed(False)
        self.grid.set_items([])
        self.status.setText("")
        self.pager.hide()
        self.bottom_previous.hide()
        self.bottom_next.hide()
        self.limit_note.hide()

    def _filter_changed(self) -> None:
        if self.query:
            self._start()

    def reset(self) -> None:
        self.query = ""
        self.scope_title = self.scope_query = ""
        self.title.setText("Search")
        self.box.setPlaceholderText("Titles, authors or subjects")
        self.clear_fixed.hide()
        self.kind.show()
        self.box.clear()
        self._clear_filters()
        self._clear_results()
        self.empty.set_text("Search the Internet Archive", "Every book you find here is free to read.")
        self.empty.show()
        self._header_changed()
        self.box.setFocus()

    def show_fixed(self, title: str, query: str, sort: str) -> None:
        """Show a category or a full shelf, which can then be searched and filtered further."""
        self.scope_title, self.scope_query = title, query
        self.title.setText(title)
        self.box.clear()
        self.box.setPlaceholderText(f"Search within {title}")
        self.clear_fixed.show()
        self.kind.hide()
        self._clear_filters()
        index = self.sort_box.findData(sort)
        if index >= 0:
            self.sort_box.setCurrentIndex(index)
        self.query, self.sort = query, sort
        self._header_changed()
        self._start()

    def search_for(self, text: str) -> None:
        self.reset()
        self.box.setText(text)
        self.search()

    def _year(self, edit: QLineEdit) -> int | None:
        return int(edit.text()) if edit.text().isdigit() else None

    def search(self) -> None:
        text = self.box.text().strip()
        subject = self.subject.text().strip()
        years = (self._year(self.year_from), self._year(self.year_to))
        if not self.scope_query and not text and not subject and years == (None, None):
            self.reset()
            return
        self.query = catalog.search_query(
            text, self.kind.currentData(), self.language.currentData(), subject, *years, base=self.scope_query)
        self.sort = self.sort_box.currentData()
        self._start()

    def _start(self) -> None:
        self.page_number = 1
        self._clear_results()
        self.empty.hide()
        self.status.setText("Searching…")
        self._fetch()

    def _last_page(self) -> int:
        return max(1, min(self.MAX_PAGE, -(-self.total // self.PAGE_SIZE)))

    def go_to_page(self, number: int) -> None:
        number = max(1, min(self._last_page(), number))
        self.page_box.setText(str(number))
        if number != self.page_number:
            self.page_number = number
            self.status.setText(f"Loading page {number:,}…")
            self.prepare_timer.stop()
            self.dim_timer.start()
            self._fetch()

    def _fetch(self) -> None:
        self.request_id += 1
        ticket = self.request_id

        def received(books: list[dict], total: int, error: str) -> None:
            if ticket != self.request_id:
                return
            self._set_dimmed(False)
            if error:
                self.status.setText("Couldn't load these results.")
                if self.grid.items:
                    self.ctx.toast.emit(error)
                else:
                    self.empty.set_text("Search isn't working right now", error)
                    self.empty.show()
                return
            self.total = total
            if not total:
                self._clear_results()
                self.empty.set_text("No free books found", "Try fewer words or remove a filter.")
                self.empty.show()
                return
            self.grid.set_items([CoverCard(self.ctx, b) for b in books])
            self.verticalScrollBar().setValue(0)
            self.status.setText(f"{total:,} title{'s' if total != 1 else ''}")
            self._update_pager()
            self.prepare_timer.start()

        self.ctx.search.run(self.query, received, self, sort=self.sort, rows=self.PAGE_SIZE, page=self.page_number)

    def _prepare_next_page(self) -> None:
        """Have the next page's list waiting, so Next doesn't wait on the Archive."""
        if self.isVisible() and self.page_number < self._last_page():
            self.ctx.search.run(
                self.query, lambda *_: None, self, sort=self.sort, rows=self.PAGE_SIZE, page=self.page_number + 1)

    def _set_dimmed(self, dimmed: bool) -> None:
        self.dim_timer.stop()
        self.grid_dim.setEnabled(dimmed)

    def _update_pager(self) -> None:
        last = self._last_page()
        self.pager.setVisible(last > 1)
        self.page_box.setText(str(self.page_number))
        self.page_total.setText(f"of {last:,}")
        first_page, last_page = self.page_number <= 1, self.page_number >= last
        self.previous_button.setEnabled(not first_page)
        self.next_button.setEnabled(not last_page)
        self.bottom_previous.setVisible(last > 1)
        self.bottom_next.setVisible(last > 1)
        self.bottom_previous.setEnabled(not first_page)
        self.bottom_next.setEnabled(not last_page)
        self.limit_note.setVisible(last_page and self.total > 10_000)
        self._header_changed()


class LibraryPage(Page):
    SHELVES = {
        "library": ("My Library", "Your library is empty",
                    "Books you download or import appear here. Reading a book online doesn't add it."),
        "want": ("Want to Read", "Nothing saved yet",
                 "Move the pointer over any book and press the heart to save it here."),
        "finished": ("Finished", "No finished books yet", "Books you reach the end of will collect here."),
    }

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.shelf = "library"
        self.collection: int | None = None
        header = QHBoxLayout()
        self.title = label("Library", "H1")
        header.addWidget(self.title)
        self.collection_menu = tool_button("more", "Collection options")
        self.collection_menu.clicked.connect(self._collection_options)
        header.addWidget(self.collection_menu)
        header.addStretch(1)
        self.sort = QComboBox()
        for text, value in (("Recent", "recent"), ("Title", "title"), ("Author", "author"), ("Date Added", "added")):
            self.sort.addItem(text, value)
        self.sort.activated.connect(self.refresh)
        header.addWidget(self.sort)
        importer = QPushButton("Import…")
        importer.setToolTip("Add EPUB or PDF files from your computer")
        importer.clicked.connect(self.import_files)
        header.addWidget(importer)
        self.body.addLayout(header)
        self.filter_row = QWidget()
        filters = QHBoxLayout(self.filter_row)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        for text, kind in (("All", ""), ("Books", "text"), ("Audiobooks", "audio")):
            button = QPushButton(text)
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.setChecked(not kind)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("kind", kind)
            button.clicked.connect(self.refresh)
            self.filter_group.addButton(button)
            filters.addWidget(button)
        filters.addStretch(1)
        self.body.addWidget(self.filter_row)
        self.empty = EmptyState("")
        self.body.addWidget(self.empty)
        self.grid = FlowGrid()
        self.body.addWidget(self.grid)
        self.body.addStretch(1)
        self.timer = debounce(self, 200, self.refresh)
        ctx.library.library_changed.connect(self.timer.start)
        ctx.library.collections_changed.connect(self.timer.start)

    def show_shelf(self, shelf: str, collection: int | None = None) -> None:
        self.shelf = shelf
        self.collection = collection
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        library = self.ctx.library
        sort = self.sort.currentData()
        if self.shelf == "collection":
            match = next((c for c in library.collections() if c["id"] == self.collection), None)
            if match is None:
                self.show_shelf("library")
                return
            title, empty, detail = match["name"], "This collection is empty", \
                "Right-click any book and choose Add to Collection."
            books = library.books("collection", sort, self.collection)
        else:
            title, empty, detail = self.SHELVES[self.shelf]
            books = library.books(self.shelf, sort)
        self.filter_row.setVisible(self.shelf == "library")
        kind = self.filter_group.checkedButton().property("kind")
        if self.shelf == "library" and kind:
            books = [b for b in books if b["kind"] == kind]
            empty, detail = "Nothing here yet", "Download something and it will appear here."
        self.title.setText(title)
        self.collection_menu.setVisible(self.shelf == "collection")
        position = self.verticalScrollBar().value()
        self.grid.set_items([CoverCard(self.ctx, b, show_progress=True) for b in books])
        self.verticalScrollBar().setValue(position)
        self.empty.set_text(empty, detail)
        self.empty.setVisible(not books)

    def _collection_options(self) -> None:
        menu = QMenu(self)
        menu.addAction("Rename…", self._rename)
        menu.addAction("Delete Collection…", self._delete)
        menu.exec(self.collection_menu.mapToGlobal(self.collection_menu.rect().bottomLeft()))

    def _rename(self) -> None:
        name, ok = QInputDialog.getText(self, "Rename Collection", "Name:", text=self.title.text())
        if ok and name.strip():
            self.ctx.library.rename_collection(self.collection, name)
            self.refresh()

    def _delete(self) -> None:
        answer = QMessageBox.question(
            self, "Delete Collection",
            f"Delete “{self.title.text()}”? The books themselves aren't affected.")
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.library.delete_collection(self.collection)
            self.ctx.show_shelf.emit("library", None)

    def import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Import Books", "", "Books (*.epub *.pdf)")
        self.import_paths(files)

    def import_paths(self, files: list[str]) -> None:
        added = 0
        problems = []
        for path in files:
            try:
                self.ctx.import_file(path)
                added += 1
            except (BookError, OSError) as error:
                problems.append(str(error))
        if problems:
            QMessageBox.warning(self, "Import", "\n".join(problems))
        if added:
            self.ctx.toast.emit(f"Imported {added} book{'s' if added != 1 else ''}")
            self.ctx.show_shelf.emit("library", None)


class GoalRing(QWidget):
    """Today's minutes against the daily goal, as a single progress ring."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(176, 176)
        self.minutes = 0.0
        self.goal = 20

    def set_values(self, minutes: float, goal: int) -> None:
        self.minutes, self.goal = minutes, goal
        self.setAccessibleName(f"{int(minutes)} of {goal} minutes read today")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(11, 11, 154, 154)
        pen = QPen(theme.color("track"), 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        fraction = min(1.0, self.minutes / self.goal) if self.goal else 0
        if fraction > 0:
            pen.setColor(theme.color("accent"))
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, int(-fraction * 360 * 16))
        painter.setPen(theme.color("text"))
        painter.setFont(theme.serif(32))
        painter.drawText(QRectF(0, 46, 176, 52), Qt.AlignmentFlag.AlignCenter, str(int(self.minutes)))
        painter.setFont(self.font())
        painter.setPen(theme.color("text2"))
        met = fraction >= 1
        painter.drawText(QRectF(0, 98, 176, 24), Qt.AlignmentFlag.AlignCenter,
                         "Goal met" if met else f"of {self.goal} min")


class WeekChart(QWidget):
    """Minutes read per day for the last seven days."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.days: list[tuple[date, float]] = []
        self.goal = 20
        self.hovered = -1

    def set_values(self, days: list[tuple[date, float]], goal: int) -> None:
        self.days, self.goal = days, goal
        description = "; ".join(f"{d.strftime('%A')}: {int(m)} minutes" for d, m in days)
        self.setAccessibleDescription(description)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(8, 26, self.width() - 16, self.height() - 52)

    def _max(self) -> float:
        return max([self.goal * 1.15] + [m for _d, m in self.days] + [1.0])

    def _column(self, index: int) -> QRectF:
        plot = self._plot()
        width = plot.width() / max(1, len(self.days))
        return QRectF(plot.left() + index * width, 0, width, self.height())

    def paintEvent(self, event) -> None:
        if not self.days:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        top = self._max()
        painter.setPen(QPen(theme.color("line"), 1))
        painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        goal_y = plot.bottom() - plot.height() * self.goal / top
        painter.setPen(QPen(theme.color("text3"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(plot.left(), goal_y), QPointF(plot.right(), goal_y))
        small = self.font()
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        painter.setFont(small)
        painter.setPen(theme.color("text2"))
        painter.drawText(QRectF(plot.left(), goal_y - 17, 120, 15), Qt.AlignmentFlag.AlignLeft,
                         f"Goal {self.goal} min")
        peak = max(range(len(self.days)), key=lambda i: self.days[i][1])
        today_index = len(self.days) - 1
        for index, (day, minutes) in enumerate(self.days):
            column = self._column(index)
            bar_w = min(30.0, column.width() * 0.5)
            x = column.center().x() - bar_w / 2
            height = plot.height() * minutes / top
            painter.setPen(Qt.PenStyle.NoPen)
            if minutes > 0:
                height = max(4.0, height)
                accent = theme.color("accent")
                if index == self.hovered:
                    accent = accent.lighter(115)
                painter.setBrush(accent)
                radius = min(4.0, height / 2)
                bar = QRectF(x, plot.bottom() - height, bar_w, height)
                painter.drawRoundedRect(bar, radius, radius)
                painter.drawRect(QRectF(x, plot.bottom() - radius, bar_w, radius))
                if (index in (peak, today_index)) and minutes >= 1:
                    painter.setPen(theme.color("text"))
                    painter.drawText(QRectF(column.left(), plot.bottom() - height - 17, column.width(), 15),
                                     Qt.AlignmentFlag.AlignHCenter, str(int(minutes)))
            weekday = day.strftime("%a")[:2]
            font = small
            font.setWeight(font.Weight.DemiBold if index == today_index else font.Weight.Normal)
            painter.setFont(font)
            painter.setPen(theme.color("text" if index == today_index else "text2"))
            painter.drawText(QRectF(column.left(), plot.bottom() + 6, column.width(), 16),
                             Qt.AlignmentFlag.AlignHCenter, weekday)

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        hovered = -1
        for index in range(len(self.days)):
            if self._column(index).contains(position):
                hovered = index
                break
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()
            if hovered >= 0:
                day, minutes = self.days[hovered]
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"{day.strftime('%A, %b')} {day.day}\n{int(minutes)} min", self)
            else:
                QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        self.hovered = -1
        self.update()


class ActivityCalendar(QWidget):
    """A year of reading at a glance: one square a day, darker for more time."""

    CELL = 13
    GAP = 3
    LEFT = 26
    TOP = 18
    FOOT = 26

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self.TOP + 7 * (self.CELL + self.GAP) + self.FOOT)
        self.setMouseTracking(True)
        self.minutes: dict[date, float] = {}
        self.goal = 20
        self.hovered: date | None = None

    def set_values(self, minutes: dict[date, float], goal: int) -> None:
        self.minutes, self.goal = minutes, goal
        days = [d for d, m in minutes.items() if m >= 1]
        self.setAccessibleDescription(
            f"Reading activity: read on {len(days)} days in the period shown, "
            f"{round(sum(minutes.values())):,} minutes in total.")
        self.update()

    def _weeks(self) -> int:
        return max(4, min(53, (self.width() - self.LEFT + self.GAP) // (self.CELL + self.GAP)))

    def _first_day(self) -> date:
        today = date.today()
        return today - timedelta(days=today.weekday()) - timedelta(weeks=self._weeks() - 1)

    def _cell(self, week: int, weekday: int) -> QRectF:
        step = self.CELL + self.GAP
        return QRectF(self.LEFT + week * step, self.TOP + weekday * step, self.CELL, self.CELL)

    def _shade(self, level: int) -> QColor:
        track, accent = theme.color("track"), theme.color("accent")
        amount = (0, 0.32, 0.55, 0.78, 1.0)[level]
        return QColor(
            round(track.red() + (accent.red() - track.red()) * amount),
            round(track.green() + (accent.green() - track.green()) * amount),
            round(track.blue() + (accent.blue() - track.blue()) * amount))

    def _level(self, minutes: float) -> int:
        if minutes < 1:
            return 0
        share = minutes / max(1, self.goal)
        return 1 if share < 0.34 else 2 if share < 0.67 else 3 if share < 1 else 4

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = self.font()
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        painter.setFont(small)
        today = date.today()
        first = self._first_day()
        painter.setPen(theme.color("text2"))
        for weekday, name in ((0, "M"), (2, "W"), (4, "F")):
            painter.drawText(QRectF(0, self._cell(0, weekday).top() - 1, self.LEFT - 6, self.CELL + 2),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
        last_month_x = -100.0
        for week in range(self._weeks()):
            monday = first + timedelta(weeks=week)
            if monday.day <= 7 and self._cell(week, 0).left() - last_month_x > 34:
                last_month_x = self._cell(week, 0).left()
                painter.setPen(theme.color("text2"))
                painter.drawText(QRectF(last_month_x, 0, 40, 15), Qt.AlignmentFlag.AlignLeft, monday.strftime("%b"))
            for weekday in range(7):
                day = monday + timedelta(days=weekday)
                if day > today:
                    continue
                cell = self._cell(week, weekday)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._shade(self._level(self.minutes.get(day, 0))))
                painter.drawRoundedRect(cell, 3, 3)
                if day == today or day == self.hovered:
                    painter.setPen(QPen(theme.color("text"), 1.4))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(cell.adjusted(0.7, 0.7, -0.7, -0.7), 3, 3)
        y = self.height() - 15
        x = self.LEFT + self._weeks() * (self.CELL + self.GAP) - self.GAP - 5 * 14 - 34
        painter.setPen(theme.color("text2"))
        painter.drawText(QRectF(x - 40, y - 3, 36, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "Less")
        painter.setPen(Qt.PenStyle.NoPen)
        for level in range(5):
            painter.setBrush(self._shade(level))
            painter.drawRoundedRect(QRectF(x + level * 14, y, 11, 11), 3, 3)
        painter.setPen(theme.color("text2"))
        painter.drawText(QRectF(x + 5 * 14 + 2, y - 3, 34, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "More")

    def _day_at(self, position: QPointF) -> date | None:
        step = self.CELL + self.GAP
        week = int((position.x() - self.LEFT) // step)
        weekday = int((position.y() - self.TOP) // step)
        if not (0 <= week < self._weeks() and 0 <= weekday < 7):
            return None
        day = self._first_day() + timedelta(weeks=week, days=weekday)
        return day if day <= date.today() else None

    def mouseMoveEvent(self, event) -> None:
        day = self._day_at(event.position())
        if day == self.hovered:
            return
        self.hovered = day
        self.update()
        if day:
            minutes = int(self.minutes.get(day, 0))
            text = f"{day.strftime('%a, %b')} {day.day}, {day.year}\n" + (f"{minutes} min" if minutes else "No reading")
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        self.hovered = None
        self.update()


class Stepper(QWidget):
    """Minus, a value, plus. Quicker and tidier than a spin box for a goal."""

    changed = Signal(int)

    def __init__(self, unit: str, step: int, low: int, high: int, name: str) -> None:
        super().__init__()
        self.unit, self.step, self.low, self.high = unit, step, low, high
        self.value = low
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.minus = tool_button("minus", f"Lower {name}", 15)
        self.plus = tool_button("plus", f"Raise {name}", 15)
        self.text = label("", "StepValue")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMinimumWidth(68)
        row.addWidget(self.minus)
        row.addWidget(self.text)
        row.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self._move(-step))
        self.plus.clicked.connect(lambda: self._move(step))

    def set_value(self, value: int) -> None:
        self.value = value
        self.text.setText(f"{value} {self.unit}")
        self.minus.setEnabled(value > self.low)
        self.plus.setEnabled(value < self.high)

    def _move(self, delta: int) -> None:
        # A step lands on a round number, so 23 goes to 25 and then 30.
        target = self.value + delta
        if self.step > 1:
            target = (self.value + (self.step - 1 if delta > 0 else -(self.step - 1))) // self.step * self.step \
                if self.value % self.step else target
            if self.value % self.step and delta < 0:
                target = self.value - self.value % self.step
        self.set_value(max(self.low, min(self.high, target)))
        self.changed.emit(self.value)


def _card(margins: tuple[int, int, int, int] = (20, 16, 20, 16)) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(6)
    return frame, layout


class GoalsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.body.setSpacing(16)
        self.body.addWidget(label("Reading Goals", "H1"))
        self.summary = label("", "Lead", wrap=True)
        self.body.addWidget(self.summary)

        top = QHBoxLayout()
        top.setSpacing(14)
        today_card, today = _card()
        today.addWidget(label("Today", "H2"))
        self.ring = GoalRing()
        today.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        goal_row = QHBoxLayout()
        goal_row.addWidget(label("Daily goal", "Muted"))
        goal_row.addStretch(1)
        self.daily = Stepper("min", 5, 5, 600, "daily goal")
        goal_row.addWidget(self.daily)
        today.addLayout(goal_row)
        today_card.setFixedWidth(290)
        top.addWidget(today_card)

        week_card, week = _card()
        week.addWidget(label("This Week", "H2"))
        self.week_caption = label("", "Muted")
        week.addWidget(self.week_caption)
        self.chart = WeekChart()
        week.addWidget(self.chart, 1)
        top.addWidget(week_card, 1)
        self.body.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        streak_card, streak = _card()
        self.streak_value = label("0", "TileValue")
        streak.addWidget(self.streak_value)
        streak.addWidget(label("day streak", "Muted"))
        streak.addStretch(1)
        self.best_streak = label("", "Faint")
        streak.addWidget(self.best_streak)
        tiles.addWidget(streak_card, 1)

        books_card, books = _card()
        self.books_value = label("0", "TileValue")
        books.addWidget(self.books_value)
        books.addWidget(label("books finished this year", "Muted"))
        self.books_bar = QProgressBar()
        self.books_bar.setTextVisible(False)
        self.books_bar.setFixedHeight(5)
        self.books_bar.setRange(0, 1000)
        books.addWidget(self.books_bar)
        year_row = QHBoxLayout()
        year_row.addWidget(label("Yearly goal", "Faint"))
        year_row.addStretch(1)
        self.yearly = Stepper("books", 1, 1, 1000, "yearly goal")
        year_row.addWidget(self.yearly)
        books.addLayout(year_row)
        tiles.addWidget(books_card, 1)

        time_card, clock = _card()
        self.time_value = label("0", "TileValue")
        clock.addWidget(self.time_value)
        self.time_caption = label("read this year", "Muted")
        clock.addWidget(self.time_caption)
        clock.addStretch(1)
        self.days_read = label("", "Faint")
        clock.addWidget(self.days_read)
        tiles.addWidget(time_card, 1)
        self.body.addLayout(tiles)

        calendar_card, calendar = _card()
        calendar.addWidget(label("Reading Activity", "H2"))
        self.calendar = ActivityCalendar()
        calendar.addWidget(self.calendar)
        self.body.addWidget(calendar_card)

        self.finished_shelf = Shelf(ctx, "Finished This Year")
        self.finished_shelf.hide()
        self.body.addWidget(self.finished_shelf)
        self.body.addWidget(label("Time counts while a book is open and you're active, or while an audiobook plays.", "Faint"))
        self.body.addStretch(1)
        self.daily.set_value(ctx.library.daily_goal())
        self.yearly.set_value(ctx.library.yearly_goal())
        self.daily.changed.connect(self._goal_changed)
        self.yearly.changed.connect(self._goal_changed)
        ctx.library.activity_changed.connect(self.refresh)
        ctx.library.library_changed.connect(self.refresh)
        self.finished_ids: list[str] = []
        theme.changed.connect(self.update)

    def _goal_changed(self) -> None:
        self.ctx.library.set_setting("daily_goal", self.daily.value)
        self.ctx.library.set_setting("yearly_goal", self.yearly.value)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        library = self.ctx.library
        today = date.today()
        goal = library.daily_goal()
        minutes = library.minutes_on(today)
        streak = library.streak()
        self.ring.set_values(minutes, goal)
        left = goal - int(minutes)
        if left <= 0:
            days = f" That's {streak} days in a row." if streak > 1 else ""
            self.summary.setText(f"You met today's goal.{days}")
        elif minutes >= 1:
            self.summary.setText(f"{left} more minute{'s' if left != 1 else ''} to reach today's goal.")
        else:
            self.summary.setText(f"A few pages today gets you going. Your goal is {goal} minutes.")

        week = library.last_days(7)
        self.chart.set_values(week, goal)
        total = sum(m for _d, m in week)
        self.week_caption.setText(f"{int(total)} min read, about {int(total / 7)} a day")

        self.streak_value.setText(str(streak))
        best = max(library.best_streak(), streak)
        self.best_streak.setText(f"Best: {best} day{'s' if best != 1 else ''}")
        finished = library.finished_in_year(today.year)
        target = library.yearly_goal()
        self.books_value.setText(f"{finished} of {target}")
        self.books_bar.setValue(int(min(1.0, finished / max(1, target)) * 1000))
        year_minutes = library.total_minutes_in_year(today.year)
        if year_minutes >= 60:
            self.time_value.setText(f"{year_minutes / 60:,.0f} h" if year_minutes >= 600 else f"{year_minutes / 60:.1f} h")
        else:
            self.time_value.setText(f"{int(year_minutes)} min")
        activity = library.activity_since(today - timedelta(weeks=54))
        this_year = [d for d, m in activity.items() if d.year == today.year and m >= 1]
        self.days_read.setText(f"On {len(this_year)} day{'s' if len(this_year) != 1 else ''} so far")
        self.calendar.set_values(activity, goal)

        books = library.finished_books(today.year)
        ids = [b["id"] for b in books]
        if ids != self.finished_ids:
            self.finished_ids = ids
            self.finished_shelf.set_books(books)
        self.finished_shelf.setVisible(bool(books))


class SettingsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.body.addWidget(label("Settings", "H1"))
        self.body.addWidget(label("Appearance", "H2"))
        row = QHBoxLayout()
        row.addWidget(label("Theme"))
        self.mode = QComboBox()
        for text, value in (("Match System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.mode.addItem(text, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(ctx.library.setting("theme", "system"))))
        self.mode.activated.connect(self._set_theme)
        row.addWidget(self.mode)
        row.addStretch(1)
        self.body.addLayout(row)

        self.body.addWidget(label("Storage", "H2"))
        self.storage = label("", "Muted", wrap=True)
        self.body.addWidget(self.storage)
        clear = QPushButton("Clear Temporary Copies")
        clear.setToolTip("Removes books kept temporarily while reading without downloading. Your downloads are not affected.")
        clear.clicked.connect(self._clear)
        self.body.addWidget(clear, 0, Qt.AlignmentFlag.AlignLeft)

        self.body.addWidget(label("Content", "H2"))
        self.hide_adult = QCheckBox("Hide adult content")
        self.hide_adult.setChecked(ctx.library.setting("hide_adult", True))
        self.hide_adult.setToolTip("Applies to Home, Browse and Search. Books already in your library stay.")
        self.hide_adult.toggled.connect(self._set_hide_adult)
        self.body.addWidget(self.hide_adult, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(label(
            "Filters by the titles and subjects the Internet Archive lists, so some items may slip through.",
            "Muted", wrap=True))

        self.body.addWidget(label("Privacy", "H2"))
        self.body.addWidget(label(
            "Erase what you've read: Continue Reading, reading positions, finished books, "
            "reading time and streaks, and temporary copies.", "Muted", wrap=True))
        forget = QPushButton("Clear Reading History…")
        forget.setToolTip("Downloads, imports, Want to Read, collections, highlights and bookmarks are kept.")
        forget.clicked.connect(self._clear_history)
        self.body.addWidget(forget, 0, Qt.AlignmentFlag.AlignLeft)

        self.body.addWidget(label("About", "H2"))
        self.body.addWidget(label(
            f"{APP_NAME} {__version__}. Free and without accounts or tracking; your library stays on this computer.\n\n"
            "Books and audiobooks come from the Internet Archive, including Project Gutenberg texts and "
            "LibriVox recordings. The Archive is a nonprofit that runs on donations.",
            "Muted", wrap=True))
        self.body.addStretch(1)

    def _set_hide_adult(self, hide: bool) -> None:
        self.ctx.library.set_setting("hide_adult", hide)
        catalog.set_adult_filter(hide)
        self.ctx.content_filter_changed.emit()

    def _set_theme(self) -> None:
        value = self.mode.currentData()
        self.ctx.library.set_setting("theme", value)
        theme.apply(value)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        kept = paths.dir_size(paths.downloads_dir())
        temporary = paths.dir_size(paths.cache_dir())
        self.storage.setText(f"Downloads and imports: {format_bytes(kept)}\nTemporary copies and cached images: {format_bytes(temporary)}")

    def _remove_temporary(self) -> None:
        shutil.rmtree(paths.stream_dir(), ignore_errors=True)
        paths.stream_dir()
        self.ctx.net.nam.cache().clear()
        self.refresh()

    def _clear(self) -> None:
        self._remove_temporary()
        self.ctx.toast.emit("Temporary copies cleared")

    def _clear_history(self) -> None:
        answer = QMessageBox.question(
            self, "Clear Reading History",
            "Erase your reading history, positions in every book, finished books, reading time "
            "and streaks?\n\nDownloads, imports, Want to Read, collections, highlights and "
            "bookmarks are kept. This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.ctx.library.clear_history()
        self._remove_temporary()
        self.ctx.toast.emit("Reading history cleared")


__all__ = ["BrowsePage", "DiscoverPage", "SearchPage", "LibraryPage", "GoalsPage", "SettingsPage", "QSize", "QColor", "Signal"]
