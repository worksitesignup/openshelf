#!/usr/bin/env bash
# Package dist/OpenShelf (the PyInstaller output) as a single AppImage file.
# Usage: packaging/build-appimage.sh OUTPUT.AppImage
set -euo pipefail
project="$(dirname "$(dirname "$(readlink -f "$0")")")"
output="$(readlink -f "$1")"
tool_url="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
tool_sha256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"

work="$project/build/appimage"
rm -rf "$work"
appdir="$work/OpenShelf.AppDir"
mkdir -p "$appdir/usr/lib"
cp -a "$project/dist/OpenShelf" "$appdir/usr/lib/openshelf"
install -m 755 "$project/packaging/AppRun" "$appdir/AppRun"
cp "$project/openshelf/assets/icon.svg" "$appdir/openshelf.svg"
cat > "$appdir/openshelf.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Open Shelf
Comment=Free books and audiobooks from the Internet Archive
Exec=openshelf
Icon=openshelf
Terminal=false
Categories=Office;Viewer;Education;
StartupWMClass=openshelf
EOF

tool="$work/appimagetool"
curl -fsSL -o "$tool" "$tool_url"
echo "$tool_sha256  $tool" | sha256sum --check --quiet
chmod +x "$tool"
# Extract-and-run lets this work on build machines without FUSE.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$tool" --no-appstream "$appdir" "$output"
