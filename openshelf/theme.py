"""Colors, type and icons. Every screen reads its colors from here."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon, QLinearGradient, QPainter, QPalette, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from . import paths

LIGHT = {
    "bg": "#FFFFFF", "sidebar": "#F5F5F3", "surface": "#F5F5F3", "raised": "#FFFFFF",
    "text": "#1C1C1E", "text2": "#66666C", "text3": "#8E8E93", "line": "#E4E4E0",
    "hover": "#EBEBE7", "accent": "#0F766E", "on_accent": "#FFFFFF", "accent_soft": "#DCEFEC",
    "track": "#E7E7E3", "skeleton": "#EEEEEA",
}
DARK = {
    "bg": "#1A1A1C", "sidebar": "#222224", "surface": "#262628", "raised": "#2C2C2F",
    "text": "#F2F2F3", "text2": "#A6A6AC", "text3": "#7C7C83", "line": "#37373A",
    "hover": "#323235", "accent": "#2DD4BF", "on_accent": "#06201D", "accent_soft": "#17403B",
    "track": "#3A3A3D", "skeleton": "#2C2C2F",
}

SERIF_FAMILIES = ["Iowan Old Style", "Palatino Linotype", "Georgia", "Noto Serif", "DejaVu Serif"]

ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5.5h4v5.5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "library": '<path d="M5 4v16"/><path d="M9.5 4v16"/><path d="m14 4.5 5 15"/>',
    "want": '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20Z"/>',
    "want_fill": '<path fill="currentColor" d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20Z"/>',
    "cloud": '<path d="M7.5 18.5a4 4 0 0 1-.6-8 5.5 5.5 0 0 1 10.6-.8 4.5 4.5 0 0 1-.5 8.8Z"/>',
    "compass": '<circle cx="12" cy="12" r="8.500"/><path d="m15.500 8.500-2 5-5 2 2-5Z"/>',
    "minus": '<path d="M6 12h12"/>',
    "flame": '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3 2.1-4.8 3.4-7 .3 1.6 1 2.6 2 3.2C11 8 11.7 5.300 13.500 3c.2 2.700 5 5.800 5 11.800 0 3.600-2.600 6.200-6.500 6.200Z"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.300l3.300 2"/>',
    "check": '<path d="m4.5 12.5 5 5 10-11"/>',
    "download": '<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>',
    "file": '<path d="M6 3.5h8l4.5 4.5v12.5H6z"/><path d="M14 3.5V8h4.5"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3.5" y="14" width="4" height="6.5" rx="1.5"/><rect x="16.5" y="14" width="4" height="6.5" rx="1.5"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2.1 2.1M15.9 15.9 18 18M18 6l-2.1 2.1M8.1 15.9 6 18"/>',
    "folder": '<path d="M3.5 7h6l2 2.5h9v10h-17z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "back": '<path d="m14.5 5-7 7 7 7"/>',
    "forward": '<path d="m9.5 5 7 7-7 7"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "list": '<path d="M8.5 6.5H20M8.5 12H20M8.5 17.5H20"/><path d="M4 6.5h.5M4 12h.5M4 17.5h.5"/>',
    "note": '<path d="M5 19.5 6 15 16.5 4.5l3.2 3.2L9.2 18.2z"/><path d="m14.5 6.5 3.2 3.2"/>',
    "bookmark": '<path d="M6.5 3.5h11v17L12 16.5l-5.5 4z"/>',
    "type": '<path d="M3 19 8 6l5 13"/><path d="M4.8 14.5h6.4"/><path d="M15 15a3 3 0 1 0 6 0 3 3 0 1 0-6 0"/><path d="M21 12v7"/>',
    "play": '<path fill="currentColor" stroke="none" d="M7 4.5v15l12-7.5z"/>',
    "pause": '<path fill="currentColor" stroke="none" d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/>',
    "prev": '<path fill="currentColor" stroke="none" d="M18 5v14L8 12z"/><path d="M6 5v14"/>',
    "next": '<path fill="currentColor" stroke="none" d="M6 5v14l10-7z"/><path d="M18 5v14"/>',
    "more": '<circle cx="5.5" cy="12" r="1" fill="currentColor"/><circle cx="12" cy="12" r="1" fill="currentColor"/><circle cx="18.5" cy="12" r="1" fill="currentColor"/>',
    "fullscreen": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
}


def is_dark_system() -> bool:
    hints = QGuiApplication.styleHints()
    if hints.colorScheme() != Qt.ColorScheme.Unknown:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


class Theme(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.mode = "system"
        self.colors = dict(LIGHT)
        self.dark = False
        self._icon_cache: dict[tuple, QIcon] = {}

    def __getitem__(self, key: str) -> str:
        return self.colors[key]

    def color(self, key: str) -> QColor:
        return QColor(self.colors[key])

    def apply(self, mode: str | None = None) -> None:
        if mode:
            self.mode = mode
        self.dark = self.mode == "dark" or (self.mode == "system" and is_dark_system())
        self.colors = dict(DARK if self.dark else LIGHT)
        self._icon_cache.clear()
        app = QApplication.instance()
        palette = QPalette()
        for role, key in (
            (QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
            (QPalette.ColorRole.Base, "raised"), (QPalette.ColorRole.AlternateBase, "surface"),
            (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface"),
            (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
            (QPalette.ColorRole.HighlightedText, "on_accent"), (QPalette.ColorRole.ToolTipBase, "raised"),
            (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.PlaceholderText, "text3"),
        ):
            palette.setColor(role, self.color(key))
        app.setPalette(palette)
        app.setStyleSheet(self.stylesheet())
        self.changed.emit()

    def serif(self, size: float, bold: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(SERIF_FAMILIES)
        font.setPointSizeF(size)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return font

    def serif_css(self) -> str:
        return ",".join(f'"{f}"' for f in SERIF_FAMILIES) + ",serif"

    def icon(self, name: str, color: str | None = None, size: int = 20) -> QIcon:
        tint = color or self["text"]
        key = (name, tint, size)
        if key in self._icon_cache:
            return self._icon_cache[key]
        body = ICONS[name].replace("currentColor", tint)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{tint}" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        pixmap = QPixmap(QSize(size * 3, size * 3))
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(3)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon = QIcon(pixmap)
        self._icon_cache[key] = icon
        return icon

    def _chevron(self, up: bool = False) -> str:
        direction = "up" if up else "down"
        shape = "m2 6.5 3-3 3 3" if up else "m2 3.5 3 3 3-3"
        path = paths.cache_dir() / f"chevron-{direction}-{self['text2'].lstrip('#')}.svg"
        if not path.exists():
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" fill="none" '
                f'stroke="{self["text2"]}" stroke-width="1.5" stroke-linecap="round" '
                'stroke-linejoin="round">'
                f'<path d="{shape}"/></svg>')
        return path.as_posix()

    def stylesheet(self) -> str:
        c = self.colors
        return f"""
        QWidget {{ color: {c['text']}; }}
        QMainWindow, #Page, #PageContent {{ background: {c['bg']}; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']}; padding: 4px 8px; }}
        #Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['line']}; }}
        #Brand {{ font-family: {self.serif_css()}; font-size: 19px; font-weight: 600; padding: 2px 8px 12px; }}
        #SideHeader {{ color: {c['text3']}; font-size: 11px; font-weight: 600; padding: 12px 10px 4px; }}
        QPushButton#SideItem {{ text-align: left; border: none; border-radius: 8px; padding: 7px 10px; background: transparent; font-size: 13px; }}
        QPushButton#SideItem:hover {{ background: {c['hover']}; }}
        QPushButton#SideItem:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
        #H1 {{ font-family: {self.serif_css()}; font-size: 30px; font-weight: 600; }}
        #H2 {{ font-family: {self.serif_css()}; font-size: 20px; font-weight: 600; }}
        #DetailTitle {{ font-family: {self.serif_css()}; font-size: 32px; font-weight: 600; }}
        #Muted {{ color: {c['text2']}; }}
        #Faint {{ color: {c['text3']}; font-size: 12px; }}
        #Body {{ font-size: 14px; line-height: 150%; }}
        #Banner {{ background: {c['surface']}; border-radius: 10px; }}
        #Tile {{ background: {c['surface']}; border-radius: 14px; }}
        #TileValue {{ font-family: {self.serif_css()}; font-size: 34px; font-weight: 600; }}
        QPushButton {{ border: 1px solid {c['line']}; border-radius: 9px; padding: 7px 14px; background: {c['raised']}; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:disabled {{ color: {c['text3']}; background: {c['surface']}; }}
        QPushButton#Primary {{ background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 600; padding: 9px 26px; }}
        QPushButton#Primary:hover {{ background: {QColor(c['accent']).lighter(112 if self.dark else 118).name()}; }}
        QPushButton#Primary:disabled {{ background: {c['track']}; color: {c['text3']}; }}
        QPushButton#Link, QPushButton#Chip {{ border: none; background: transparent; color: {c['accent']}; padding: 2px 4px; }}
        QPushButton#Link:hover {{ text-decoration: underline; background: transparent; }}
        QPushButton#Chip {{ background: {c['surface']}; border-radius: 12px; padding: 4px 11px; color: {c['text2']}; font-size: 12px; }}
        QPushButton#Chip:checked {{ background: {c['accent_soft']}; color: {c['accent']}; }}
        #HeroNumber {{ font-family: {self.serif_css()}; font-size: 58px; font-weight: 600; color: {c['accent']}; }}
        #Lead {{ font-size: 16px; color: {c['text2']}; }}
        #TileName {{ font-family: {self.serif_css()}; font-size: 15px; font-weight: 600; }}
        QPushButton#CategoryTile {{ background: {c['surface']}; border: none; border-radius: 12px; padding: 0; }}
        QPushButton#CategoryTile:hover {{ background: {c['hover']}; }}
        #PinnedHeader {{ background: {c['bg']}; border-bottom: 1px solid {c['line']}; }}
        #StepValue {{ font-size: 13px; font-weight: 600; }}
        #Card {{ background: {c['surface']}; border-radius: 14px; }}
        QPushButton#Chip:hover {{ background: {c['hover']}; color: {c['text']}; }}
        QPushButton#Author {{ border: none; background: transparent; color: {c['accent']}; font-size: 17px; padding: 0; text-align: left; }}
        QPushButton#Author:hover {{ text-decoration: underline; background: transparent; }}
        QToolButton {{ border: none; border-radius: 8px; padding: 5px; background: transparent; }}
        QToolButton:hover {{ background: {c['hover']}; }}
        QToolButton:checked {{ background: {c['accent_soft']}; }}
        QToolButton::menu-indicator {{ image: none; }}
        QLineEdit, QSpinBox {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 6px 10px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
        QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 20px; border: none; background: transparent; }}
        QSpinBox::up-button {{ subcontrol-position: top right; }}
        QSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {c['hover']}; }}
        QSpinBox::up-arrow {{ image: url({self._chevron(up=True)}); width: 8px; height: 8px; }}
        QSpinBox::down-arrow {{ image: url({self._chevron()}); width: 8px; height: 8px; }}
        QLineEdit:focus, QSpinBox:focus {{ border: 1.5px solid {c['accent']}; }}
        QLineEdit#SearchBox {{ font-size: 16px; padding: 11px 14px; border-radius: 11px; background: {c['surface']}; border-color: transparent; }}
        QComboBox {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 5px 10px; min-width: 70px; }}
        QComboBox:hover {{ background: {c['hover']}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox::down-arrow {{ image: url({self._chevron()}); width: 10px; height: 10px; }}
        QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['line']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; outline: 0; }}
        QMenu {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 5px; }}
        QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {c['accent_soft']}; }}
        QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 6px; }}
        QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {c['track']}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {c['accent']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['track']}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {c['text3']}; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; width: 0; background: none; }}
        QScrollBar:horizontal {{ height: 0; }}
        #PlayerBar {{ background: {c['sidebar']}; border-top: 1px solid {c['line']}; }}
        QListWidget {{ background: transparent; border: none; outline: 0; }}
        QListWidget::item {{ padding: 8px 8px; border-radius: 7px; }}
        QListWidget::item:hover {{ background: {c['hover']}; }}
        QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
        """


theme = Theme()


def typographic_cover(title: str, author: str, width: int, height: int, dpr: float = 2.0) -> QPixmap:
    """A designed cover for books whose scan has no usable one."""
    palettes = [
        ("#23395B", "#EDE6D6"), ("#5B2333", "#F4E9DC"), ("#2F4B3C", "#EFE9D9"),
        ("#6B4A2B", "#F7EFE2"), ("#3C3556", "#ECE7F2"), ("#7A3B2E", "#F9EEE3"),
        ("#1F5560", "#E6F0EE"), ("#44403C", "#F0ECE4"),
    ]
    seed = sum((i + 1) * ord(ch) for i, ch in enumerate(title + author))
    background, ink = palettes[seed % len(palettes)]
    pixmap = QPixmap(int(width * dpr), int(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0, QColor(background).lighter(112))
    gradient.setColorAt(1, QColor(background))
    painter.fillRect(0, 0, width, height, gradient)
    pad = width * 0.11
    painter.setPen(QColor(ink))
    long_title = title if len(title) < 90 else title[:87].rstrip() + "…"
    flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    box = QRectF(pad, height * 0.13, width - 2 * pad, height * 0.58)
    size = max(9.0, width * 0.115)
    # Shrink until the longest word fits on a line and the whole title fits the box.
    words = long_title.split() or [""]
    while size > 6:
        metrics = QFontMetricsF(theme.serif(size))
        fits_wide = max(metrics.horizontalAdvance(w) for w in words) <= box.width()
        if fits_wide and metrics.boundingRect(box, flags, long_title).height() <= box.height():
            break
        size *= 0.93
    painter.setFont(theme.serif(size))
    painter.drawText(box, flags, long_title)
    rule_y = height * 0.76
    painter.fillRect(QRectF(pad, rule_y, width * 0.2, max(1.0, width * 0.012)), QColor(ink))
    painter.setFont(theme.serif(max(7.0, width * 0.07), bold=False))
    painter.drawText(
        QRectF(pad, rule_y + height * 0.03, width - 2 * pad, height * 0.17),
        int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
        author,
    )
    painter.end()
    return pixmap


def format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def format_clock(seconds: float) -> str:
    total = int(max(0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_hours(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hr {minutes} min" if hours else f"{minutes} min"


__all__ = ["theme", "typographic_cover", "format_bytes", "format_clock", "format_hours", "QFontDatabase"]
