# Third-party software in Open Shelf downloads

Open Shelf itself is licensed under the GNU GPL v3 (see `LICENSE`). Its source code is at
https://github.com/worksitesignup/openshelf.

Packaged downloads also contain:

| Software | License | Source |
| --- | --- | --- |
| Qt 6, including Qt WebEngine, Qt PDF and Qt Multimedia | LGPL v3 (`LGPL-3.0.txt`) | https://code.qt.io |
| PySide6 and Shiboken6 | LGPL v3 (`LGPL-3.0.txt`) | https://code.qt.io/cgit/pyside/pyside-setup.git |
| Python | PSF License | https://www.python.org/downloads/source/ |

Qt WebEngine includes Chromium, and Qt Multimedia may include FFmpeg, each with its own
open-source components and notices. See https://doc.qt.io/qt-6/licenses-used-in-qt.html.

The Qt libraries are kept as separate files, so you can replace them with your own builds.
On macOS they are inside `Open Shelf.app/Contents/Frameworks`. On Linux, run the AppImage
with `--appimage-extract` to unpack it.
