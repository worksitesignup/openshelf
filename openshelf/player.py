"""Audiobook playback and the player bar along the bottom of the main window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from . import catalog
from .context import AppContext
from .theme import format_clock, theme
from .widgets import label, tool_button

SPEEDS = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


class AudioPlayer(QObject):
    track_changed = Signal()
    state_changed = Signal(bool)
    position_changed = Signal(float, float)
    failed = Signal(str)

    SAVE_EVERY = 5

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.ctx = ctx
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.book: dict | None = None
        self.index = 0
        self.pending_seek: float | None = None
        self.player.mediaStatusChanged.connect(self._status)
        self.player.playbackStateChanged.connect(self._state)
        self.player.positionChanged.connect(self._position)
        self.player.errorOccurred.connect(self._error)
        self.player.setPlaybackRate(float(ctx.library.setting("audio_speed", 1.0)))
        self.output.setVolume(float(ctx.library.setting("audio_volume", 1.0)))
        self.ticker = QTimer(self)
        self.ticker.setInterval(self.SAVE_EVERY * 1000)
        self.ticker.timeout.connect(self._tick)

    @property
    def tracks(self) -> list[dict]:
        return (self.book or {}).get("plan", {}).get("tracks", [])

    @property
    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def resume_book(self, book: dict) -> None:
        saved = (book.get("position") or {}).get("audio") or {}
        index = int(saved.get("track", 0))
        seconds = float(saved.get("seconds", 0))
        if book.get("finished_at"):
            index, seconds = 0, 0
        self.play_book(book, index, seconds)

    def play_book(self, book: dict, index: int, seconds: float = 0) -> None:
        self.save()
        self.book = self.ctx.library.book(book["id"]) or book
        self.ctx.library.mark_opened(book["id"])
        self._load(index, seconds)

    def _source(self, index: int) -> QUrl:
        track = self.tracks[index]
        local = ((self.book or {}).get("local") or {}).get("tracks", {}).get(track["name"])
        if local and Path(local).exists():
            return QUrl.fromLocalFile(local)
        return QUrl(catalog.file_url(self.book["id"], track["name"]))  # type: ignore[index]

    def _load(self, index: int, seconds: float) -> None:
        if not 0 <= index < len(self.tracks):
            return
        self.index = index
        self.pending_seek = seconds if seconds > 1 else None
        self.player.setSource(self._source(index))
        self.player.play()
        self.ticker.start()
        self.track_changed.emit()

    def toggle(self) -> None:
        if self.playing:
            self.player.pause()
            self.save()
        else:
            self.player.play()

    def stop(self) -> None:
        self.save()
        self.player.stop()
        self.player.setSource(QUrl())
        self.book = None
        self.ticker.stop()
        self.track_changed.emit()

    def next(self) -> None:
        if self.index + 1 < len(self.tracks):
            self._load(self.index + 1, 0)

    def previous(self) -> None:
        if self.player.position() > 5000 or self.index == 0:
            self.player.setPosition(0)
        else:
            self._load(self.index - 1, 0)

    def skip(self, seconds: int) -> None:
        self.player.setPosition(max(0, self.player.position() + seconds * 1000))

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))

    def set_speed(self, rate: float) -> None:
        self.player.setPlaybackRate(rate)
        self.ctx.library.set_setting("audio_speed", rate)

    def _progress(self) -> float:
        tracks = self.tracks
        total = sum(t.get("seconds", 0) for t in tracks)
        current = self.player.position() / 1000
        if not total:
            return (self.index + 0.5) / max(1, len(tracks))
        return (sum(t.get("seconds", 0) for t in tracks[: self.index]) + current) / total

    def save(self) -> None:
        if self.book and self.player.source().isValid():
            position = {"track": self.index, "seconds": self.player.position() / 1000}
            self.ctx.library.save_position(self.book["id"], "audio", position, self._progress())

    def _tick(self) -> None:
        if self.playing and self.book:
            self.ctx.library.add_activity(self.book["id"], self.SAVE_EVERY)
            self.save()

    def _status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if self.pending_seek is not None:
                self.player.setPosition(int(self.pending_seek * 1000))
                self.pending_seek = None
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.index + 1 < len(self.tracks):
                self._load(self.index + 1, 0)
            else:
                self.save()
                if self.book:
                    self.ctx.library.set_finished(self.book["id"], True)

    def _state(self, _state) -> None:
        self.state_changed.emit(self.playing)

    def _position(self, milliseconds: int) -> None:
        self.position_changed.emit(milliseconds / 1000, self.player.duration() / 1000)

    def _error(self, _error, message: str) -> None:
        if message:
            self.failed.emit("This audio couldn't be played. Check your connection and try again.")


class PlayerBar(QWidget):
    def __init__(self, ctx: AppContext, player: AudioPlayer) -> None:
        super().__init__()
        self.setObjectName("PlayerBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.ctx = ctx
        self.player = player
        self.dragging = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)
        self.art = QLabel()
        self.art.setFixedSize(38, 56)
        self.art.setCursor(Qt.CursorShape.PointingHandCursor)
        self.art.mousePressEvent = lambda _e: self.ctx.show_detail.emit(player.book) if player.book else None
        layout.addWidget(self.art)
        words = QVBoxLayout()
        words.setSpacing(1)
        self.title = label("")
        self.track = label("", "Muted")
        words.addStretch(1)
        words.addWidget(self.title)
        words.addWidget(self.track)
        words.addStretch(1)
        holder = QWidget()
        holder.setLayout(words)
        holder.setFixedWidth(230)
        layout.addWidget(holder)
        self.previous = tool_button("prev", "Previous chapter")
        self.previous.clicked.connect(player.previous)
        self.play = tool_button("pause", "Play or pause", 24)
        self.play.clicked.connect(player.toggle)
        self.next = tool_button("next", "Next chapter")
        self.next.clicked.connect(player.next)
        for button in (self.previous, self.play, self.next):
            layout.addWidget(button)
        self.time = label("0:00", "Muted")
        layout.addWidget(self.time)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("Position in chapter")
        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderReleased.connect(self._released)
        layout.addWidget(self.slider, 1)
        self.remaining = label("0:00", "Muted")
        layout.addWidget(self.remaining)
        self.speed = QComboBox()
        self.speed.setToolTip("Playback speed")
        for rate in SPEEDS:
            self.speed.addItem(f"{rate:g}×", rate)
        self.speed.setCurrentIndex(max(0, self.speed.findData(float(ctx.library.setting("audio_speed", 1.0)))))
        self.speed.activated.connect(lambda: player.set_speed(self.speed.currentData()))
        layout.addWidget(self.speed)
        close = tool_button("close", "Stop and close player", 16)
        close.clicked.connect(player.stop)
        layout.addWidget(close)
        player.track_changed.connect(self._track)
        player.state_changed.connect(self._playing)
        player.position_changed.connect(self._moved)
        self.hide()

    def _pressed(self) -> None:
        self.dragging = True

    def _released(self) -> None:
        self.dragging = False
        self.player.seek(self.slider.value())

    def _track(self) -> None:
        book = self.player.book
        self.setVisible(book is not None)
        if not book:
            return
        self.title.setText(book["title"][:38])
        tracks = self.player.tracks
        self.track.setText(tracks[self.player.index]["title"][:40] if tracks else "")
        self.art.setPixmap(self.ctx.covers.fallback(book, 38, 56))
        self.art.setScaledContents(True)

        def ready(pixmap: QPixmap | None) -> None:
            if pixmap:
                self.art.setPixmap(pixmap)

        self.ctx.covers.request(book, ready, self)

    def _playing(self, playing: bool) -> None:
        self.play.setIcon(theme.icon("pause" if playing else "play", size=24))
        self.play.setIconSize(QSize(24, 24))

    def _moved(self, position: float, duration: float) -> None:
        if not self.dragging:
            self.slider.setRange(0, int(duration))
            self.slider.setValue(int(position))
        self.time.setText(format_clock(position))
        self.remaining.setText(f"-{format_clock(duration - position)}")
