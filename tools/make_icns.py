"""Build build/OpenShelf.icns from the app's SVG icon. macOS only: it needs iconutil."""
import os
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    app = QGuiApplication([])  # noqa: F841  Qt needs one alive while rendering.
    renderer = QSvgRenderer(str(ROOT / "openshelf" / "assets" / "icon.svg"))
    if not renderer.isValid():
        raise SystemExit("icon.svg could not be read")

    iconset = ROOT / "build" / "OpenShelf.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            size = base * scale
            image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            image.save(str(iconset / f"icon_{base}x{base}{suffix}.png"))

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "build" / "OpenShelf.icns")], check=True)


if __name__ == "__main__":
    main()
