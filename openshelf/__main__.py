"""Start Open Shelf."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtWebEngineWidgets  # noqa: F401  Must load before QApplication exists.
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, catalog, paths
from .textview import register_scheme


def main() -> int:
    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("OpenShelf")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("openshelf")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.svg")))

    from .context import AppContext
    from .mainwindow import MainWindow
    from .theme import theme

    paths.trim_stream_cache()
    ctx = AppContext()
    theme.apply(ctx.library.setting("theme", "system"))
    catalog.set_adult_filter(ctx.library.setting("hide_adult", True))

    def system_scheme_changed() -> None:
        if theme.mode == "system":
            theme.apply()

    QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: system_scheme_changed())
    window = MainWindow(ctx)
    window.show()
    code = app.exec()
    del window
    return code


if __name__ == "__main__":
    sys.exit(main())
