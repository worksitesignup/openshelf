"""The on-device library: books, progress, collections, highlights and reading activity."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'text', source TEXT NOT NULL DEFAULT 'ia',
  cover TEXT, year TEXT DEFAULT '', language TEXT DEFAULT '', subjects TEXT DEFAULT '[]',
  plan TEXT DEFAULT '{}', added_at REAL NOT NULL, last_opened REAL,
  progress REAL NOT NULL DEFAULT 0, position TEXT NOT NULL DEFAULT '{}', view TEXT,
  want INTEGER NOT NULL DEFAULT 0, finished_at REAL, local TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS collection_books(
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  added_at REAL NOT NULL, PRIMARY KEY(collection_id, book_id));
CREATE TABLE IF NOT EXISTS highlights(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  spine INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
  text TEXT NOT NULL, color TEXT NOT NULL DEFAULT 'yellow', note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bookmarks(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  view TEXT NOT NULL, spine INTEGER NOT NULL DEFAULT 0, offset INTEGER NOT NULL DEFAULT 0,
  page INTEGER NOT NULL DEFAULT 0, label TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS activity(
  day TEXT NOT NULL, book_id TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, book_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_highlights_book ON highlights(book_id, spine);
CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id);
"""

_JSON_FIELDS = {"subjects": [], "plan": {}, "position": {}, "local": {}}
FINISHED_AT = 0.985
STARTED_AT = 0.02  # Far enough in that a book has stopped being something you want to read.


def _book(row: sqlite3.Row) -> dict:
    book = dict(row)
    for field, default in _JSON_FIELDS.items():
        try:
            book[field] = json.loads(book[field] or "null") or default
        except (TypeError, ValueError):
            book[field] = default
    return book


class Library(QObject):
    book_changed = Signal(str)
    library_changed = Signal()
    collections_changed = Signal()
    activity_changed = Signal()

    def __init__(self, path: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(bookmarks)")}
        if "note" not in columns:
            self.conn.execute("ALTER TABLE bookmarks ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _write(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        cursor = self.conn.execute(sql, args)
        self.conn.commit()
        return cursor

    # Settings
    def setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._write(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # Books
    def add_book(self, book: dict) -> dict:
        """Insert or refresh catalog details, never touching the reader's own state."""
        subjects = json.dumps(book.get("subjects") or [])
        plan = json.dumps(book.get("plan") or {})
        self._write(
            """INSERT INTO books(id,title,author,kind,source,cover,year,language,subjects,plan,added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, author=excluded.author,
                 year=excluded.year, language=excluded.language,
                 subjects=CASE WHEN excluded.subjects='[]' THEN books.subjects ELSE excluded.subjects END,
                 plan=CASE WHEN excluded.plan='{}' THEN books.plan ELSE excluded.plan END,
                 cover=COALESCE(excluded.cover, books.cover)""",
            (book["id"], book.get("title") or "Untitled", book.get("author") or "",
             book.get("kind", "text"), book.get("source", "ia"), book.get("cover"),
             book.get("year", ""), book.get("language", ""), subjects, plan, time.time()),
        )
        self.library_changed.emit()
        self.book_changed.emit(book["id"])
        return self.book(book["id"])  # type: ignore[return-value]

    def book(self, book_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return _book(row) if row else None

    def has_book(self, book_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone() is not None

    def remove_book(self, book_id: str) -> None:
        self._write("DELETE FROM books WHERE id=?", (book_id,))
        self.conn.execute("DELETE FROM activity WHERE book_id=? AND seconds=0", (book_id,))
        self.conn.commit()
        self.library_changed.emit()
        self.book_changed.emit(book_id)

    def books(self, shelf: str = "all", sort: str = "recent", collection: int | None = None) -> list[dict]:
        """List books. "library" is what the reader chose to keep: downloads and imports.
        Every other book is only streamed, and reached through Continue Reading or search."""
        where = {
            "all": "1=1",
            "reading": "last_opened IS NOT NULL AND finished_at IS NULL AND progress < 0.985",
            "want": "want=1",
            "finished": "finished_at IS NOT NULL",
            "library": "local NOT IN ('{}','')",
            "collection": "id IN (SELECT book_id FROM collection_books WHERE collection_id=?)",
        }[shelf]
        order = {
            "recent": "COALESCE(last_opened, added_at) DESC",
            "title": "title COLLATE NOCASE ASC",
            "author": "author COLLATE NOCASE ASC",
            "added": "added_at DESC",
        }[sort]
        args = (collection,) if shelf == "collection" else ()
        rows = self.conn.execute(f"SELECT * FROM books WHERE {where} ORDER BY {order}", args).fetchall()
        return [_book(r) for r in rows]

    def mark_opened(self, book_id: str) -> None:
        self._write("UPDATE books SET last_opened=? WHERE id=?", (time.time(), book_id))
        self.library_changed.emit()

    def save_position(self, book_id: str, view: str, position: dict, progress: float) -> None:
        """Store where the reader is. Reaching the end marks the book finished once."""
        book = self.book(book_id)
        if not book:
            return
        merged = dict(book["position"])
        merged[view] = position
        progress = max(0.0, min(1.0, progress))
        self._write(
            "UPDATE books SET position=?, progress=?, view=? WHERE id=?",
            (json.dumps(merged), progress, view, book_id),
        )
        if progress >= FINISHED_AT and not book["finished_at"]:
            self.set_finished(book_id, True)
        elif progress >= STARTED_AT and book["want"]:
            self.set_want(book_id, False)  # It now lives in Continue Reading.
        self.book_changed.emit(book_id)

    def clear_recent(self, book_id: str) -> None:
        """Take a book off Continue Reading. Its place and notes are kept for next time."""
        self._write("UPDATE books SET last_opened=NULL WHERE id=?", (book_id,))
        self.library_changed.emit()
        self.book_changed.emit(book_id)

    def clear_history(self) -> None:
        """Forget what was read, when and how far. Books that were downloaded, imported,
        shelved or annotated stay; ones that were only streamed go with the history."""
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM books").fetchall()]
        with self.conn:
            self.conn.execute("DELETE FROM activity")
            self.conn.execute(
                "UPDATE books SET last_opened=NULL, progress=0, position='{}', view=NULL, finished_at=NULL")
            self.conn.execute(
                """DELETE FROM books WHERE local IN ('{}','') AND want=0
                   AND id NOT IN (SELECT book_id FROM collection_books)
                   AND id NOT IN (SELECT book_id FROM highlights)
                   AND id NOT IN (SELECT book_id FROM bookmarks)""")
        self.library_changed.emit()
        self.activity_changed.emit()
        for book_id in ids:
            self.book_changed.emit(book_id)

    def set_view(self, book_id: str, view: str) -> None:
        self._write("UPDATE books SET view=? WHERE id=?", (view, book_id))

    def set_want(self, book_id: str, want: bool) -> None:
        self._write("UPDATE books SET want=? WHERE id=?", (int(want), book_id))
        self.library_changed.emit()
        self.book_changed.emit(book_id)

    def set_finished(self, book_id: str, finished: bool) -> None:
        self._write(
            "UPDATE books SET finished_at=?, want=CASE WHEN ?=1 THEN 0 ELSE want END WHERE id=?",
            (time.time() if finished else None, int(finished), book_id),
        )
        self.library_changed.emit()
        self.activity_changed.emit()
        self.book_changed.emit(book_id)

    def set_local(self, book_id: str, local: dict) -> None:
        self._write("UPDATE books SET local=? WHERE id=?", (json.dumps(local), book_id))
        self.library_changed.emit()
        self.book_changed.emit(book_id)

    def set_cover(self, book_id: str, cover: str) -> None:
        self._write("UPDATE books SET cover=? WHERE id=?", (cover, book_id))
        self.book_changed.emit(book_id)

    # Collections
    def collections(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM collections ORDER BY name COLLATE NOCASE").fetchall()]

    def create_collection(self, name: str) -> int:
        name = name.strip()
        row = self.conn.execute("SELECT id FROM collections WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        new_id = self._write("INSERT INTO collections(name) VALUES(?)", (name,)).lastrowid
        self.collections_changed.emit()
        return int(new_id)  # type: ignore[arg-type]

    def rename_collection(self, collection_id: int, name: str) -> None:
        self._write("UPDATE collections SET name=? WHERE id=?", (name.strip(), collection_id))
        self.collections_changed.emit()

    def delete_collection(self, collection_id: int) -> None:
        self._write("DELETE FROM collections WHERE id=?", (collection_id,))
        self.collections_changed.emit()
        self.library_changed.emit()

    def add_to_collection(self, collection_id: int, book_id: str) -> None:
        self._write(
            "INSERT OR IGNORE INTO collection_books(collection_id,book_id,added_at) VALUES(?,?,?)",
            (collection_id, book_id, time.time()),
        )
        self.library_changed.emit()

    def remove_from_collection(self, collection_id: int, book_id: str) -> None:
        self._write("DELETE FROM collection_books WHERE collection_id=? AND book_id=?",
                    (collection_id, book_id))
        self.library_changed.emit()

    def book_collections(self, book_id: str) -> set[int]:
        rows = self.conn.execute(
            "SELECT collection_id FROM collection_books WHERE book_id=?", (book_id,)).fetchall()
        return {r["collection_id"] for r in rows}

    # Highlights
    def highlights(self, book_id: str, spine: int | None = None) -> list[dict]:
        if spine is None:
            rows = self.conn.execute(
                "SELECT * FROM highlights WHERE book_id=? ORDER BY spine, start", (book_id,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM highlights WHERE book_id=? AND spine=? ORDER BY start", (book_id, spine))
        return [dict(r) for r in rows.fetchall()]

    def add_highlight(self, book_id: str, spine: int, start: int, end: int, text: str,
                      color: str = "yellow", note: str = "") -> int:
        return int(self._write(
            "INSERT INTO highlights(book_id,spine,start,end,text,color,note,created) VALUES(?,?,?,?,?,?,?,?)",
            (book_id, spine, start, end, text, color, note, time.time()),
        ).lastrowid)  # type: ignore[arg-type]

    def update_highlight(self, highlight_id: int, color: str | None = None, note: str | None = None) -> None:
        if color is not None:
            self._write("UPDATE highlights SET color=? WHERE id=?", (color, highlight_id))
        if note is not None:
            self._write("UPDATE highlights SET note=? WHERE id=?", (note, highlight_id))

    def delete_highlight(self, highlight_id: int) -> None:
        self._write("DELETE FROM highlights WHERE id=?", (highlight_id,))

    # Bookmarks
    def bookmarks(self, book_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM bookmarks WHERE book_id=? ORDER BY view, spine, offset, page",
            (book_id,)).fetchall()]

    def add_bookmark(self, book_id: str, view: str, spine: int, offset: int, page: int, label: str) -> int:
        return int(self._write(
            "INSERT INTO bookmarks(book_id,view,spine,offset,page,label,created) VALUES(?,?,?,?,?,?,?)",
            (book_id, view, spine, offset, page, label, time.time()),
        ).lastrowid)  # type: ignore[arg-type]

    def update_bookmark(self, bookmark_id: int, note: str) -> None:
        self._write("UPDATE bookmarks SET note=? WHERE id=?", (note, bookmark_id))

    def delete_bookmark(self, bookmark_id: int) -> None:
        self._write("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))

    # Reading activity and goals
    def add_activity(self, book_id: str, seconds: int, day: date | None = None) -> None:
        self._write(
            "INSERT INTO activity(day,book_id,seconds) VALUES(?,?,?) "
            "ON CONFLICT(day,book_id) DO UPDATE SET seconds=seconds+excluded.seconds",
            ((day or date.today()).isoformat(), book_id, seconds),
        )
        self.activity_changed.emit()

    def minutes_on(self, day: date) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(seconds),0) s FROM activity WHERE day=?", (day.isoformat(),)).fetchone()
        return row["s"] / 60.0

    def last_days(self, count: int, today: date | None = None) -> list[tuple[date, float]]:
        today = today or date.today()
        days = [today - timedelta(days=i) for i in range(count - 1, -1, -1)]
        return [(d, self.minutes_on(d)) for d in days]

    def daily_goal(self) -> int:
        return int(self.setting("daily_goal", 20))

    def yearly_goal(self) -> int:
        return int(self.setting("yearly_goal", 12))

    def streak(self, today: date | None = None) -> int:
        """Consecutive days the daily goal was met. Today only counts once it is met,
        so an unfinished today doesn't break a streak."""
        today = today or date.today()
        goal = self.daily_goal()
        day = today if self.minutes_on(today) >= goal else today - timedelta(days=1)
        count = 0
        while self.minutes_on(day) >= goal:
            count += 1
            day -= timedelta(days=1)
        return count

    def best_streak(self) -> int:
        """The longest run of consecutive days the current daily goal was met."""
        goal = self.daily_goal() * 60
        rows = self.conn.execute(
            "SELECT day FROM activity GROUP BY day HAVING SUM(seconds)>=? ORDER BY day", (goal,)).fetchall()
        best = run = 0
        previous: date | None = None
        for row in rows:
            day = date.fromisoformat(row["day"])
            run = run + 1 if previous and day - previous == timedelta(days=1) else 1
            best = max(best, run)
            previous = day
        return best

    def activity_since(self, start: date) -> dict[date, float]:
        """Minutes read per day from a date onward; days with no reading are left out."""
        rows = self.conn.execute(
            "SELECT day, SUM(seconds) s FROM activity WHERE day>=? GROUP BY day", (start.isoformat(),)).fetchall()
        return {date.fromisoformat(r["day"]): r["s"] / 60.0 for r in rows}

    def finished_books(self, year: int) -> list[dict]:
        start = time.mktime((year, 1, 1, 0, 0, 0, 0, 0, -1))
        end = time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 0, -1))
        rows = self.conn.execute(
            "SELECT * FROM books WHERE finished_at>=? AND finished_at<? ORDER BY finished_at DESC",
            (start, end)).fetchall()
        return [_book(r) for r in rows]

    def finished_in_year(self, year: int) -> int:
        start = time.mktime((year, 1, 1, 0, 0, 0, 0, 0, -1))
        end = time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 0, -1))
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM books WHERE finished_at>=? AND finished_at<?", (start, end)).fetchone()
        return int(row["c"])

    def total_minutes_in_year(self, year: int) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(seconds),0) s FROM activity WHERE day LIKE ?", (f"{year}-%",)).fetchone()
        return row["s"] / 60.0
