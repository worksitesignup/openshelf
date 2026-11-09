#!/usr/bin/env bash
# Add Open Shelf to your application menu, launching this copy of the project.
# Undo with: packaging/install-linux.sh --remove
set -euo pipefail
project="$(dirname "$(dirname "$(readlink -f "$0")")")"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$apps/openshelf.desktop" "$icons/openshelf.svg"
    echo "Removed Open Shelf from the application menu."
    exit 0
fi

if [ -x "$project/dist/OpenShelf/OpenShelf" ]; then
    command="$project/dist/OpenShelf/OpenShelf"
else
    command="$project/run.sh"
fi

mkdir -p "$apps" "$icons"
cp "$project/openshelf/assets/icon.svg" "$icons/openshelf.svg"
cat > "$apps/openshelf.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Open Shelf
Comment=Free books and audiobooks from the Internet Archive
Exec="$command" %F
Icon=openshelf
Terminal=false
Categories=Office;Viewer;Education;
MimeType=application/epub+zip;
StartupWMClass=openshelf
EOF
echo "Added Open Shelf to the application menu."
