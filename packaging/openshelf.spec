# PyInstaller recipe. The same file builds on Linux, Windows and macOS; each build
# must run on the system it targets, which is what CI build runners are for.
# Build from the project root: pyinstaller --noconfirm packaging/openshelf.spec
import re
import sys
from pathlib import Path

root = Path(SPECPATH).parent
version = re.search(r'__version__ = "(.+?)"', (root / "openshelf" / "__init__.py").read_text()).group(1)

analysis = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "openshelf" / "assets"), "openshelf/assets"),
        (str(root / "LICENSE"), "licenses"),
        (str(root / "licenses"), "licenses"),
    ],
    hiddenimports=["PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtPdf", "PySide6.QtMultimedia", "PySide6.QtSvg"],
    excludes=["tkinter", "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtDesigner"],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="OpenShelf", console=False)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, name="OpenShelf")

if sys.platform == "darwin":
    icon = root / "build" / "OpenShelf.icns"  # Made by tools/make_icns.py.
    app = BUNDLE(
        bundle,
        name="Open Shelf.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="org.openshelf.OpenShelf",
        version=version,
        info_plist={
            "CFBundleDisplayName": "Open Shelf",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "14.0",
        },
    )
