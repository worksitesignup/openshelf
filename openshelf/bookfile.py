"""Open EPUB and plain-text books as a list of chapters ready for the reader."""
from __future__ import annotations

import bisect
import mimetypes
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .sanitize import clean_css, sanitize_html

MAX_MEMBER_BYTES = 80_000_000  # A guard against zip bombs in downloaded files.
IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml", "image/bmp",
}


class BookError(Exception):
    """The file can't be read as a book."""


@dataclass
class TocEntry:
    title: str
    spine: int
    fragment: str = ""
    depth: int = 0


@dataclass
class Chapter:
    html: str
    css: str
    text: str


@dataclass
class SearchHit:
    spine: int
    offset: int
    snippet: str
    chapter: str


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    head = data[:300].decode("ascii", "ignore")
    match = re.search(r"""encoding=["']([\w.-]+)["']|charset=["']?([\w.-]+)""", head)
    if match:
        try:
            return data.decode(match.group(1) or match.group(2), "replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


class BaseBook:
    token: str = ""
    UNITS_PER_PAGE = 1700
    toc: list[TocEntry]
    weights: list[int]

    def __init__(self) -> None:
        self._cumulative: list[int] = []
        self._text_cache: dict[int, str] = {}

    @property
    def length(self) -> int:
        return len(self.weights)

    def _totals(self) -> list[int]:
        if not self._cumulative:
            total = 0
            for weight in self.weights:
                self._cumulative.append(total)
                total += weight
            self._cumulative.append(total)
        return self._cumulative

    def fraction(self, spine: int, chapter_fraction: float) -> float:
        """How far through the whole book a point in a chapter is, by text size."""
        totals = self._totals()
        if not totals[-1] or spine >= self.length:
            return 0.0
        return (totals[spine] + self.weights[spine] * chapter_fraction) / totals[-1]

    def locate(self, fraction: float) -> tuple[int, float]:
        """The inverse of fraction(): (spine index, fraction within that chapter)."""
        totals = self._totals()
        target = max(0.0, min(1.0, fraction)) * totals[-1]
        spine = min(self.length - 1, max(0, bisect.bisect_right(totals, target) - 1))
        weight = self.weights[spine] or 1
        return spine, max(0.0, min(1.0, (target - totals[spine]) / weight))

    def page_count(self) -> int:
        """Length in stable page numbers, which don't change with font or window size."""
        return max(1, self._totals()[-1] // self.UNITS_PER_PAGE)

    def page_at(self, spine: int, offset: int) -> int:
        """The stable page number of a character offset within a chapter."""
        length = max(1, len(self.chapter_text(spine)))
        fraction = self.fraction(spine, min(1.0, offset / length))
        return min(self.page_count(), int(fraction * self.page_count()) + 1)

    def chapter(self, index: int) -> Chapter:
        raise NotImplementedError

    def title_of(self, spine: int) -> str:
        """The chapter a section belongs to, or "" if a section holds several chapters
        and so can't be named from its position alone."""
        here = [entry for entry in self.toc if entry.spine == spine]
        if len(here) > 1:
            return ""
        if here:
            return here[0].title
        best = ""
        for entry in self.toc:
            if entry.spine > spine:
                break
            best = entry.title
        return best

    def chapter_text(self, index: int) -> str:
        if index not in self._text_cache:
            self._text_cache[index] = self.chapter(index).text
        return self._text_cache[index]

    def search(self, query: str, limit: int = 300) -> list[SearchHit]:
        needle = query.strip().lower()
        hits: list[SearchHit] = []
        if not needle:
            return hits
        for spine in range(self.length):
            text = self.chapter_text(spine)
            haystack = text.lower()
            position = haystack.find(needle)
            while position != -1 and len(hits) < limit:
                start = max(0, position - 60)
                snippet = re.sub(r"\s+", " ", text[start:position + len(needle) + 80]).strip()
                prefix = "…" if start else ""
                hits.append(SearchHit(spine, position, prefix + snippet + "…", self.title_of(spine)))
                position = haystack.find(needle, position + len(needle))
            if len(hits) >= limit:
                break
        return hits

    def resource(self, path: str) -> tuple[bytes, str] | None:
        return None

    def close(self) -> None:
        pass


class EpubBook(BaseBook):
    def __init__(self, path: str | Path, token: str = "") -> None:
        super().__init__()
        self.token = token
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as error:
            raise BookError(f"Not a readable EPUB file: {error}") from error
        self.names = set(self.zip.namelist())
        self.title = ""
        self.author = ""
        self.cover_path: str | None = None
        self.spine: list[str] = []
        self.toc = []
        self.weights = []
        self._css_cache: dict[str, str] = {}
        self._chapters: dict[int, Chapter] = {}
        try:
            self._load_package()
        except (ET.ParseError, KeyError, IndexError) as error:
            raise BookError(f"This EPUB is damaged: {error}") from error
        if not self.spine:
            raise BookError("This EPUB has no readable chapters.")
        self.weights = [max(1, self.zip.getinfo(p).file_size) for p in self.spine]

    def _read(self, name: str) -> bytes:
        info = self.zip.getinfo(name)
        if info.file_size > MAX_MEMBER_BYTES:
            raise BookError("A file inside this EPUB is unreasonably large.")
        return self.zip.read(info)

    def _load_package(self) -> None:
        container = ET.fromstring(self._read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if _local(e.tag) == "rootfile")
        opf_path = unquote(rootfile.attrib["full-path"])
        opf_dir = posixpath.dirname(opf_path)
        opf = ET.fromstring(self._read(opf_path))

        manifest: dict[str, dict] = {}
        spine_ids: list[str] = []
        toc_id = ""
        cover_id = ""
        for element in opf.iter():
            name = _local(element.tag)
            if name == "title" and not self.title:
                self.title = _text(element)
            elif name == "creator" and not self.author:
                self.author = _text(element)
            elif name == "meta" and element.attrib.get("name") == "cover":
                cover_id = element.attrib.get("content", "")
            elif name == "item":
                href = posixpath.normpath(posixpath.join(opf_dir, unquote(element.attrib.get("href", ""))))
                manifest[element.attrib.get("id", "")] = {
                    "href": href,
                    "type": element.attrib.get("media-type", ""),
                    "props": element.attrib.get("properties", ""),
                }
            elif name == "spine":
                toc_id = element.attrib.get("toc", "")
            elif name == "itemref":
                spine_ids.append(element.attrib.get("idref", ""))

        for item_id in spine_ids:
            item = manifest.get(item_id)
            if item and item["href"] in self.names and "html" in item["type"]:
                self.spine.append(item["href"])

        cover = manifest.get(cover_id) or next(
            (m for m in manifest.values() if "cover-image" in m["props"]), None)
        if cover and cover["href"] in self.names:
            self.cover_path = cover["href"]

        self._spine_index = {path: i for i, path in enumerate(self.spine)}
        nav = next((m for m in manifest.values() if "nav" in m["props"].split()), None)
        ncx = manifest.get(toc_id) or next(
            (m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
        for loader, item in ((self._load_ncx, ncx), (self._load_nav, nav)):
            if item and item["href"] in self.names and not self.toc:
                try:
                    loader(item["href"])
                except ET.ParseError:
                    self.toc = []
        if not self.toc:
            self.toc = [TocEntry(f"Section {i + 1}", i) for i in range(len(self.spine))]

    def _toc_target(self, base: str, href: str) -> tuple[int, str] | None:
        parts = urlsplit(href)
        path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path)))
        spine = self._spine_index.get(path)
        return None if spine is None else (spine, parts.fragment)

    def _load_ncx(self, path: str) -> None:
        root = ET.fromstring(self._read(path))

        def walk(parent: ET.Element, depth: int) -> None:
            for point in parent:
                if _local(point.tag) != "navpoint":
                    continue
                label, source = "", ""
                for child in point:
                    if _local(child.tag) == "navlabel":
                        label = _text(child)
                    elif _local(child.tag) == "content":
                        source = child.attrib.get("src", "")
                target = self._toc_target(path, source)
                if target and label:
                    self.toc.append(TocEntry(label, target[0], target[1], depth))
                walk(point, depth + 1)

        nav_map = next((e for e in root.iter() if _local(e.tag) == "navmap"), None)
        if nav_map is not None:
            walk(nav_map, 0)

    def _load_nav(self, path: str) -> None:
        root = ET.fromstring(self._read(path))
        nav = None
        for element in root.iter():
            if _local(element.tag) == "nav" and any(
                k.endswith("type") and v == "toc" for k, v in element.attrib.items()
            ):
                nav = element
                break
        if nav is None:
            return

        def walk(parent: ET.Element, depth: int) -> None:
            for item in parent:
                if _local(item.tag) != "li":
                    continue
                for child in item:
                    name = _local(child.tag)
                    if name == "a":
                        target = self._toc_target(path, child.attrib.get("href", ""))
                        if target and _text(child):
                            self.toc.append(TocEntry(_text(child), target[0], target[1], depth))
                    elif name == "ol":
                        walk(child, depth + 1)

        for child in nav:
            if _local(child.tag) == "ol":
                walk(child, 0)

    def _resolve(self, base: str, href: str) -> tuple[str, str]:
        parts = urlsplit(href)
        path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path)))
        return path, parts.fragment

    def _stylesheet(self, base: str, href: str) -> str:
        path, _fragment = self._resolve(base, href)
        if path not in self._css_cache:
            self._css_cache[path] = clean_css(_decode(self._read(path))) if path in self.names else ""
        return self._css_cache[path]

    def chapter(self, index: int) -> Chapter:
        if index in self._chapters:
            return self._chapters[index]
        base = self.spine[index]

        def image(source: str) -> str | None:
            if urlsplit(source).scheme:
                return None  # Never fetch remote images on a book's behalf.
            path, _fragment = self._resolve(base, source)
            return f"osbook:/{self.token}/{quote(path)}" if path in self.names else None

        def link(href: str) -> dict[str, str]:
            parts = urlsplit(href)
            if parts.scheme in ("http", "https"):
                return {"data-os-ext": href, "href": "#"}
            if parts.scheme:
                return {}
            if not parts.path:
                return {"data-os-spine": str(index), "data-os-frag": parts.fragment, "href": "#"}
            path, fragment = self._resolve(base, href)
            spine = self._spine_index.get(path)
            if spine is None:
                return {}
            return {"data-os-spine": str(spine), "data-os-frag": fragment, "href": "#"}

        html, text, styles = sanitize_html(_decode(self._read(base)), image, link)
        css = "\n".join(self._stylesheet(base, s) for s in styles)
        chapter = Chapter(html, f"@scope (#os-book) {{\n{css}\n}}" if css.strip() else "", text)
        if len(self._chapters) > 12:
            self._chapters.pop(next(iter(self._chapters)))
        self._chapters[index] = chapter
        return chapter

    def resource(self, path: str) -> tuple[bytes, str] | None:
        path = posixpath.normpath(path).lstrip("/")
        if path not in self.names:
            return None
        mime = mimetypes.guess_type(path)[0] or ""
        if mime not in IMAGE_TYPES:
            return None
        return self._read(path), mime

    def cover_image(self) -> bytes | None:
        if not self.cover_path:
            return None
        try:
            return self._read(self.cover_path)
        except (KeyError, BookError):
            return None

    def close(self) -> None:
        self.zip.close()


def parse_ocr_pages(data: str) -> list[str]:
    """Per-page text from an Internet Archive '_djvu.xml' file, for searching scans."""
    pages = []
    for block in re.findall(r"<OBJECT\b.*?</OBJECT>", data, re.S):
        words = re.findall(r"<WORD\b[^>]*>([^<]*)</WORD>", block)
        pages.append(unescape(" ".join(words)))
    return pages


_CHAPTER_LINE = re.compile(
    r"^\s*(?:(?:chapter|book|part|canto|letter|act)\s+(?:[ivxlcdm]+|\d+|[a-z]+)\b.{0,70}|[IVXLC]{1,7}\.?)\s*$",
    re.I,
)
_MAX_SECTION = 45_000


class TextBook(BaseBook):
    """A plain .txt file (or OCR text), split into readable sections."""

    def __init__(self, path: str | Path, title: str = "", token: str = "", ocr: bool = False) -> None:
        super().__init__()
        self.token = token
        self.title = title
        self.author = ""
        self.cover_path = None
        raw = _decode(Path(path).read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
        raw = raw.replace("\f", "\n\n")
        if ocr:
            raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        if not paragraphs:
            raise BookError("This text file is empty.")
        self.sections: list[list[str]] = [[]]
        self.toc = []
        size = 0
        for paragraph in paragraphs:
            is_heading = len(paragraph) < 90 and "\n" not in paragraph and bool(_CHAPTER_LINE.match(paragraph))
            if (is_heading and size > 800) or size > _MAX_SECTION:
                self.sections.append([])
                size = 0
            if is_heading:
                self.toc.append(TocEntry(paragraph.title(), len(self.sections) - 1))
            self.sections[-1].append(paragraph)
            size += len(paragraph)
        self.weights = [max(1, sum(len(p) for p in s)) for s in self.sections]
        if len(self.toc) < 2:
            self.toc = [TocEntry(f"Section {i + 1}", i) for i in range(len(self.sections))]
        elif self.toc[0].spine > 0:
            self.toc.insert(0, TocEntry("Beginning", 0))

    def chapter(self, index: int) -> Chapter:
        parts = []
        texts = []
        for paragraph in self.sections[index]:
            flat = re.sub(r"\s*\n\s*", " ", paragraph)
            texts.append(flat)
            if len(flat) < 90 and _CHAPTER_LINE.match(flat):
                parts.append(f"<h2>{escape(flat)}</h2>")
            else:
                parts.append(f"<p>{escape(flat)}</p>")
        return Chapter("".join(parts), "", "".join(texts))
