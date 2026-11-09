"""The book page: details, primary actions, chapters for audiobooks, other editions."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from . import catalog
from .context import AppContext
from .net import Downloads
from .theme import format_bytes, format_clock, format_hours, theme
from .widgets import FlowGrid, Page, Shelf, label


class _Cover(QWidget):
    def __init__(self, width: int = 230) -> None:
        super().__init__()
        self.setFixedWidth(width)
        self.setFixedHeight(int(width * 1.5))
        self.pixmap: QPixmap | None = None

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        if not self.pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        ratio = self.pixmap.height() / max(1, self.pixmap.width())
        height = min(self.height() - 12, int((self.width() - 12) * ratio))
        rect = QRect(6, 2, self.width() - 12, height)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(6, 0, -1):
            painter.setBrush(Qt.GlobalColor.black)
            painter.setOpacity(0.035)
            painter.drawRoundedRect(rect.adjusted(-i, 0, i, i * 2), 6, 6)
        painter.setOpacity(1)
        painter.drawPixmap(rect, self.pixmap)


class DetailPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.book: dict = {}
        self.plan: dict = {}
        self.ticket = 0
        self.tail_widgets: list[QWidget] = []

        top = QHBoxLayout()
        top.setSpacing(30)
        self.cover = _Cover()
        top.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(8)
        self.title = label("", "DetailTitle", wrap=True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.title)
        self.author = QPushButton()
        self.author.setObjectName("Author")
        self.author.setCursor(Qt.CursorShape.PointingHandCursor)
        self.author.clicked.connect(lambda: self.ctx.search_text.emit(self.book.get("author", "")))
        info.addWidget(self.author, 0, Qt.AlignmentFlag.AlignLeft)
        self.meta = label("", "Muted", wrap=True)
        info.addWidget(self.meta)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.primary = QPushButton("Read")
        self.primary.setObjectName("Primary")
        self.primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary.clicked.connect(self._open)
        self.download = QPushButton("Download")
        self.download.clicked.connect(self._download_clicked)
        self.want = QPushButton("Want to Read")
        self.want.clicked.connect(self._toggle_want)
        self.more_button = QPushButton("More")
        self.more_button.clicked.connect(self._more)
        for button in (self.primary, self.download, self.want, self.more_button):
            actions.addWidget(button)
        actions.addStretch(1)
        info.addSpacing(6)
        info.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setMaximumWidth(380)
        self.progress.hide()
        info.addWidget(self.progress)
        self.view_note = label("", "Faint", wrap=True)
        info.addWidget(self.view_note)
        info.addSpacing(6)
        self.description = label("", "Body", wrap=True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        info.addWidget(self.description)
        self.expand = QPushButton("More")
        self.expand.setObjectName("Link")
        self.expand.clicked.connect(self._expand)
        info.addWidget(self.expand, 0, Qt.AlignmentFlag.AlignLeft)
        self.chips = FlowChips(ctx)
        info.addWidget(self.chips)
        info.addStretch(1)
        top.addLayout(info, 1)
        self.body.addLayout(top)

        self.tracks_title = label("Chapters", "H2")
        self.body.addWidget(self.tracks_title)
        self.tracks = QListWidget()
        self.tracks.itemActivated.connect(self._track_chosen)
        self.tracks.itemClicked.connect(self._track_chosen)
        self.tracks.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.addWidget(self.tracks)
        self.tail = QVBoxLayout()
        self.body.addLayout(self.tail)
        self.body.addStretch(1)

        ctx.library.book_changed.connect(self._book_changed)
        ctx.downloads.progress.connect(self._download_progress)
        ctx.downloads.finished.connect(self._download_finished)
        ctx.downloads.failed.connect(self._download_failed)

    def show_book(self, book: dict) -> None:
        self.ticket += 1
        ticket = self.ticket
        stored = self.ctx.library.book(book["id"])
        self.book = stored or dict(book)
        self.plan = (stored or {}).get("plan") or book.get("plan") or {}
        self.verticalScrollBar().setValue(0)
        self.title.setText(self.book.get("title", ""))
        self.author.setText(self.book.get("author", ""))
        self.meta.setText("")
        self.description_full = False
        self.description.setText("")
        self.expand.hide()
        self.tracks.clear()
        self.tracks.hide()
        self.tracks_title.hide()
        self.chips.set_subjects([])
        self._clear_tail()
        self.cover.set_pixmap(self.ctx.covers.fallback(self.book, 218, 327))
        self.ctx.covers.request(self.book, lambda p: self._cover_ready(p, ticket), self)
        self._render()
        if self.book.get("source") == "local":
            return
        self.primary.setEnabled(bool(self.plan.get("available")))

        def received(payload: object | None, error: str) -> None:
            if ticket != self.ticket:
                return
            if isinstance(payload, dict) and payload.get("metadata"):
                self.plan = catalog.analyze_item(payload)
                if self.ctx.library.has_book(self.book["id"]):
                    self.book = self.ctx.ensure_in_library(self._record(), self.plan)
                self._render()
                self._load_editions()
            elif not self.plan:
                self.view_note.setText(error or "Couldn't load this book.")

        self.ctx.net.get_json(catalog.metadata_url(self.book["id"]), received, self)

    def refresh_state(self) -> None:
        """Bring the buttons up to date after returning from the reader."""
        stored = self.ctx.library.book(self.book.get("id", ""))
        if stored:
            self.book = stored
        self._render()

    def _record(self) -> dict:
        record = dict(self.book)
        record.update({k: self.plan[k] for k in ("title", "author", "year", "language", "kind") if self.plan.get(k)})
        if self.plan.get("subjects"):
            record["subjects"] = self.plan["subjects"]
        return record

    def _cover_ready(self, pixmap: QPixmap | None, ticket: int) -> None:
        if ticket == self.ticket and pixmap:
            self.cover.set_pixmap(pixmap)

    def _clear_tail(self) -> None:
        while self.tail.count():
            widget = self.tail.takeAt(0).widget()
            if widget:
                widget.deleteLater()

    def _book_changed(self, book_id: str) -> None:
        if self.book and book_id == self.book.get("id") and self.isVisible():
            stored = self.ctx.library.book(book_id)
            if stored:
                self.book = stored
            else:
                self.book = {k: v for k, v in self.book.items()
                             if k not in ("want", "progress", "local", "finished_at")}
            self._render()

    def _is_audio(self) -> bool:
        return (self.plan.get("kind") or self.book.get("kind")) == "audio"

    def _render(self) -> None:
        plan, book = self.plan, self.book
        audio = self._is_audio()
        if plan.get("title") and book.get("source") != "local":
            self.title.setText(plan["title"])
            self.author.setText(plan["author"])
        progress = book.get("progress") or 0
        verb = "Listen" if audio else "Read"
        if book.get("finished_at"):
            verb = "Listen Again" if audio else "Read Again"
        elif progress > 0.001:
            verb = f"Continue · {round(progress * 100)}%"
        self.primary.setText(verb)
        self.primary.setEnabled(bool(plan.get("available")))

        facts = []
        if plan.get("year") or book.get("year"):
            facts.append(plan.get("year") or book.get("year"))
        if plan.get("publisher"):
            facts.append(plan["publisher"])
        if plan.get("pages"):
            facts.append(f"{plan['pages']} pages")
        if audio and plan.get("tracks"):
            total = sum(t.get("seconds", 0) for t in plan["tracks"])
            facts.append(f"{len(plan['tracks'])} chapters")
            if total:
                facts.append(format_hours(total))
        self.meta.setText(" · ".join(str(f) for f in facts))

        stored = bool(book.get("added_at"))
        wanted = bool(book.get("want"))
        self.want.setText("In Want to Read" if wanted else "Want to Read")
        self.want.setIcon(theme.icon("want_fill" if wanted else "want", theme["accent"] if wanted else None, 15))
        self._render_download()

        note = ""
        if book.get("source") == "local":
            note = "In your library. Imported from your computer."
        elif plan and not plan.get("available"):
            note = ("This title is only available to borrow from the Archive's lending library, "
                    "so Open Shelf can't open it.")
        elif book.get("local"):
            note = "Downloaded. It's in your library and can be " + ("played" if audio else "read") + " offline."
        elif plan.get("available"):
            verb = "Plays" if audio else "Reads"
            note = f"{verb} online from the Internet Archive. Download it to keep it in your library and use it offline."
        self.view_note.setText(note)

        description = plan.get("description") or ""
        self.description_text = description
        short = description if self.description_full or len(description) <= 460 else description[:440].rsplit(" ", 1)[0] + "…"
        self.description.setText(short)
        self.expand.setVisible(len(description) > 460 and not self.description_full)
        self.chips.set_subjects((plan.get("subjects") or book.get("subjects") or [])[:8])
        self._render_tracks()
        self.more_button.setEnabled(stored or bool(plan))

    def _render_download(self) -> None:
        book, plan = self.book, self.plan
        job = self.ctx.downloads.is_active(book.get("id", ""))
        self.progress.setVisible(job)
        files = Downloads.files_for(plan) if plan else []
        if book.get("source") == "local" or not files:
            self.download.hide()
            return
        self.download.show()
        if job:
            self.download.setText("Cancel Download")
        elif book.get("local"):
            self.download.setText("Remove Download")
        else:
            total = sum(f[2] for f in files)
            self.download.setText(f"Download · {format_bytes(total)}" if total else "Download")

    def _render_tracks(self) -> None:
        tracks = self.plan.get("tracks") or []
        visible = self._is_audio() and bool(tracks)
        self.tracks.setVisible(visible)
        self.tracks_title.setVisible(visible)
        if not visible:
            return
        if self.tracks.count() == len(tracks):
            return  # Saved progress re-renders this page often; leave the list alone.
        self.tracks.clear()
        for index, track in enumerate(tracks):
            length = format_clock(track["seconds"]) if track.get("seconds") else ""
            item = QListWidgetItem(f"{index + 1}.  {track['title']}    {length}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.tracks.addItem(item)
        self.tracks.setFixedHeight(min(len(tracks), 400) * 35 + 8)

    def _load_editions(self) -> None:
        self._clear_tail()
        if self._is_audio() or self.book.get("source") == "local":
            return
        words = catalog.user_query(self.plan.get("title", "").split(":")[0])
        if not words:
            return
        title_terms = catalog._all_of(self.plan.get("title", "").split(":")[0].split(";")[0])
        query = f"{catalog.FREE_TEXTS} AND title:({title_terms})"
        shelf = Shelf(self.ctx, "Other Editions")
        self.tail.addWidget(shelf)
        ticket = self.ticket

        def received(books: list[dict], _total: int, error: str) -> None:
            if ticket != self.ticket:
                return
            others = [b for b in books if b["id"] != self.book["id"]]
            shelf.set_books(others)

        shelf.show_skeleton(6)
        # Editions should not be collapsed together here, so ask the Archive directly.
        self.ctx.net.get_json(
            catalog.search_url(query, rows=16),
            lambda payload, err: received(catalog.parse_search(payload)[0] if isinstance(payload, dict) else [], 0, err),
            shelf,
        )
        link = QPushButton("View on the Internet Archive")
        link.setObjectName("Link")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://archive.org/details/{self.book['id']}")))
        self.tail.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)

    def _expand(self) -> None:
        self.description_full = True
        self.description.setText(self.description_text)
        self.expand.hide()

    def _ensure(self) -> dict:
        self.book = self.ctx.ensure_in_library(self._record(), self.plan)
        return self.book

    def _open(self) -> None:
        if self.plan.get("available"):
            self.ctx.open_book.emit(self._ensure())

    def _track_chosen(self, item: QListWidgetItem) -> None:
        self.ctx.player.play_book(self._ensure(), item.data(Qt.ItemDataRole.UserRole), 0)

    def _toggle_want(self) -> None:
        book = self._ensure()
        self.ctx.library.set_want(book["id"], not book["want"])

    def _download_clicked(self) -> None:
        downloads = self.ctx.downloads
        book_id = self.book["id"]
        if downloads.is_active(book_id):
            downloads.cancel(book_id)
        elif self.book.get("local"):
            answer = QMessageBox.question(
                self, "Remove Download",
                "Remove this book from your library and delete the offline copy? "
                "You can still read it online, and your place and notes are kept.")
            if answer == QMessageBox.StandardButton.Yes:
                downloads.remove(self.book)
        else:
            downloads.start(self._ensure(), self.plan)
            self.progress.setValue(0)
            self._render_download()

    def _download_progress(self, book_id: str, fraction: float) -> None:
        if book_id == self.book.get("id"):
            self.progress.setValue(int(fraction * 1000))

    def _download_finished(self, book_id: str) -> None:
        if book_id == self.book.get("id"):
            self.refresh_state()

    def _download_failed(self, book_id: str, message: str) -> None:
        if book_id == self.book.get("id"):
            self._render_download()

    def _more(self) -> None:
        from .widgets import BookMenu

        menu_helper = BookMenu(self.ctx)
        menu = QMenu(self)
        library = self.ctx.library
        stored = library.book(self.book["id"])
        finished = bool(stored and stored["finished_at"])

        def toggle() -> None:
            self._ensure()
            library.set_finished(self.book["id"], not finished)

        menu.addAction("Mark as Not Finished" if finished else "Mark as Finished", toggle)
        menu_helper.fill_collections(menu.addMenu("Add to Collection"), self.book, self)
        menu_helper.add_manage_actions(menu, stored, self)
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))


class FlowChips(QWidget):
    """Subject chips that wrap onto extra lines."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.buttons: list[QPushButton] = []

    def set_subjects(self, subjects: list[str]) -> None:
        for button in self.buttons:
            button.deleteLater()
        self.buttons = []
        for subject in subjects:
            button = QPushButton(subject[:42], self)
            button.setObjectName("Chip")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.adjustSize()
            button.clicked.connect(lambda _=False, s=subject: self.ctx.search_text.emit(s))
            button.show()
            self.buttons.append(button)
        self._layout()

    def resizeEvent(self, event) -> None:
        self._layout()

    def _layout(self) -> None:
        x = y = 0
        height = 0
        for button in self.buttons:
            size: QSize = button.sizeHint()
            button.resize(size)
            if x and x + size.width() > self.width():
                x = 0
                y += size.height() + 6
            button.move(x, y)
            x += size.width() + 6
            height = y + size.height()
        self.setFixedHeight(height)


__all__ = ["DetailPage", "QLabel"]
