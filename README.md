# Open Shelf

A free desktop app for reading books and listening to audiobooks from the
[Internet Archive](https://archive.org). No account, no purchases, no tracking. Your
library, progress and notes stay on your computer.

## Download

Get the latest version from the [Releases page](https://github.com/worksitesignup/openshelf/releases/latest):

| Your computer | File |
| --- | --- |
| Mac with Apple Silicon (M1 or later) | `OpenShelf-macOS-AppleSilicon.dmg` |
| Mac with an Intel processor | `OpenShelf-macOS-Intel.dmg` |
| Linux (64-bit Intel or AMD) | `OpenShelf-Linux-x86_64.AppImage` |

**macOS** (version 14 Sonoma or later): open the disk image and drag Open Shelf to
Applications. The app isn't signed with a paid Apple developer certificate, so macOS blocks
it the first time. Try to open it, then go to System Settings > Privacy & Security, scroll
down and choose **Open Anyway**. You only need to do this once.

**Linux**: make the file executable with `chmod +x OpenShelf-Linux-x86_64.AppImage`, then
run it. Your system needs FUSE, which most distributions include.

## Run from source

Requires Python 3.10 or later.

```bash
./run.sh
```

The first run installs its dependencies into a private `.venv` folder (about 700 MB).
On Linux, run `packaging/install-linux.sh` to add it to your application menu.

## What it does

**Finding books**
- Browse the whole catalog, tens of millions of titles, by subject, century, language and
  library, with the size of each shown
- Home shelves by genre, plus trending and most popular
- Search by title, author or subject, with language, year, subject and type filters, and
  numbered pages of results
- "Because You Read" recommendations, worked out on your computer
- Only books anyone can download are shown; lending-library titles are filtered out

**Reading**
- Pages (two side by side in wide windows) or continuous scrolling
- Fonts, text size, spacing, margins, and White, Sepia, Gray and Night themes
- Highlights in four colors, bookmarks and notes, all edited in one sidebar, with page
  numbers; table of contents, search inside a book
- Born-digital books open as text; scanned books open as their original pages
- Books open in the main window. Page turns slide, and can be switched off under Appearance
- Scanned pages are fetched ahead of you until the whole book is ready, so turning pages
  doesn't wait

**Listening**
- LibriVox and other audiobooks with chapters, speed control and resume

**Your library**
- My Library holds what you download or import; reading online doesn't add anything to it
- Want to Read, Finished, your own collections
- Import your own EPUB and PDF files (or drag them onto the window)
- Reading goals: daily minutes, streaks, books per year and a calendar of your reading

**Offline**
- Books stream by default. Press Download on a cover or a book's page to keep it.
- Pages kept while streaming are temporary and can be cleared in Settings.

## Keyboard

| Key | Action |
| --- | --- |
| Left / Right, Space, Page Up / Down | Turn the page |
| Ctrl+F | Search in the book |
| Ctrl+D | Bookmark |
| Ctrl++ / Ctrl+- | Text size (zoom on scans), also in the reader's footer |
| F11 | Full screen |
| Esc | Leave full screen, or close the side panel |
| Alt+Left | Back |

On a Mac, use Cmd in place of Ctrl.

## Development

```bash
.venv/bin/python -m unittest discover tests   # Tests need no window or network.
.venv/bin/python tools/update_counts.py       # Refresh the Browse counts before a release.
```

| File | Purpose |
| --- | --- |
| `openshelf/catalog.py` | Archive queries, and deciding what can be opened and how |
| `openshelf/sanitize.py` | Makes book content safe to display |
| `openshelf/bookfile.py` | Reads EPUB and text files |
| `openshelf/db.py` | The SQLite library |
| `openshelf/net.py` | Search, streaming and downloads |
| `openshelf/reader.py`, `textview.py`, `scanview.py` | The reading screen |
| `openshelf/assets/reader.js` | Pagination, position tracking and highlights |
| `openshelf/pages.py`, `detail.py`, `player.py`, `mainwindow.py` | The other screens |

Book files are untrusted. Every chapter passes through an allow-list sanitizer, book pages
run under a strict content security policy, and the reader's browser profile has all
network access blocked.

## Building a packaged app

```bash
.venv/bin/pip install "pyinstaller>=6.16"
.venv/bin/pyinstaller --noconfirm packaging/openshelf.spec
```

The result is `dist/OpenShelf/`. Each build must be made on the system it targets, so the
downloads are built by GitHub Actions (`.github/workflows/release.yml`). Pushing a tag such
as `v0.1.0` builds them all and publishes a release.

## Credits and licensing

Books and audiobooks are provided by the Internet Archive, including texts from Project
Gutenberg and recordings from LibriVox. The Archive is a nonprofit that runs on
[donations](https://archive.org/donate).

Copyright (C) 2026 Open Shelf contributors. Open Shelf is free software under the
[GNU General Public License v3](LICENSE).

Open Shelf uses Qt through PySide6, under the LGPL v3. Packaged copies you share should
include that license notice and keep the Qt libraries as separate, replaceable files, which
the folder build above does. See [licenses/THIRD-PARTY.md](licenses/THIRD-PARTY.md).
