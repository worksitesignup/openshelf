"""Internet Archive catalog logic: building queries and reading item metadata.

Pure functions with no Qt or network code, so they can be unit tested.

The important rule lives here: Open Shelf only shows books anyone can download.
Items in the lending library look free in search results but their files are
marked private and answer 401, so they are excluded in the query and again,
file by file, in analyze_item.
"""
from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from typing import Any
from urllib.parse import quote, urlencode

BASE = "https://archive.org"
SEARCH_URL = f"{BASE}/advancedsearch.php"

TEXT_COLLECTIONS = ("gutenberg", "americana", "toronto", "cdl", "europeanlibraries")
LENDING_COLLECTIONS = ("printdisabled", "inlibrary", "internetarchivebooks")
AUDIO_COLLECTION = "librivoxaudio"

_NOT_LENDING = (
    f"NOT collection:({' OR '.join(LENDING_COLLECTIONS)}) AND NOT access-restricted-item:true"
)
FREE_TEXTS = (
    f"mediatype:texts AND collection:({' OR '.join(TEXT_COLLECTIONS)}) AND {_NOT_LENDING} "
    'AND format:(EPUB OR "Text PDF" OR DjVuTXT OR Text)'
)
# Everything that can be opened here, leaving out bulk records nobody reads as books.
_READABLE = 'format:(EPUB OR "Text PDF" OR DjVuTXT OR Text OR "Single Page Processed JP2 ZIP" OR "Image Container PDF")'
_NOT_RECORDS = (
    "NOT collection:(usfederalcourts OR newspapers OR gazetteofindia OR microfilm "
    "OR governmentpublications OR andhra-goir)"
)
ALL_TEXTS = f"mediatype:texts AND {_NOT_LENDING} AND {_READABLE} AND {_NOT_RECORDS}"
FREE_AUDIO = f"mediatype:audio AND collection:{AUDIO_COLLECTION} AND {_NOT_LENDING}"

_ENGLISH = "language:(eng OR English OR en)"
# Home shelves should be readable books, not reference works or adult material.
_SHELF_CLEAN = (
    "NOT title:(dictionary OR catalogue OR catalog OR index OR bulletin OR report OR "
    "proceedings OR kama OR perfumed OR grammar OR vocabulary OR weekly OR journal OR "
    "magazine OR transactions OR annual OR genealogy OR family) "
    "NOT subject:(erotic OR erotica OR sex OR sexual OR dictionaries OR catalogs OR genealogy "
    "OR periodicals)"
)

# Words only: "xxx" is a Roman numeral and "playboy" a classic play's title.
_ADULT_WORDS = 'erotic OR erotica OR eroticism OR porn OR pornography OR pornographic OR hentai OR nsfw OR smut'
_NOT_ADULT = (
    f'NOT title:({_ADULT_WORDS} OR "kama sutra" OR "playboy magazine") '
    f'AND NOT subject:({_ADULT_WORDS} OR "sex stories" OR "adult content")'
)
_adult_filter = False  # The app turns this on from its setting; tools count the whole catalog.

FIELDS = (
    "identifier", "title", "creator", "date", "year", "language", "subject",
    "downloads", "week", "mediatype", "collection",
)

_FICTION = 'subject:(fiction OR novel OR novels OR "short stories")'

# (title, query fragment, sort)
TEXT_SHELVES = (
    ("Trending This Week", _FICTION, "week desc"),
    ("Most Popular", _FICTION, "downloads desc"),
    ("Timeless Classics",
     "creator:(Austen OR Dickens OR Bronte OR Tolstoy OR Dostoyevsky OR Melville OR Twain "
     "OR Hardy OR Eliot OR Hugo OR Dumas OR Wilde)", "downloads desc"),
    ("Science Fiction", 'subject:("science fiction")', "downloads desc"),
    ("Mystery & Detective", 'subject:("detective and mystery" OR "mystery fiction" OR detective)',
     "downloads desc"),
    ("Ghosts & Gothic", 'subject:("horror tales" OR "ghost stories" OR "gothic fiction")',
     "downloads desc"),
    ("Adventure", 'subject:("adventure stories" OR "sea stories")', "downloads desc"),
    ("Poetry", "subject:(poetry OR poems)", "downloads desc"),
    ("Philosophy", "subject:(philosophy OR ethics OR stoics)", "downloads desc"),
    ("History & Biography", "subject:(history OR biography)", "downloads desc"),
    ("For Young Readers", 'subject:("children\'s stories" OR "juvenile fiction" OR "fairy tales")',
     "downloads desc"),
)

AUDIO_SHELVES = (
    ("Most Listened", "", "downloads desc"),
    ("Trending This Week", "", "week desc"),
    ("Classic Novels", "subject:(novel OR fiction OR literature)", "downloads desc"),
    ("Mystery & Detective", "subject:(detective OR mystery OR crime)", "downloads desc"),
    ("Science Fiction", 'subject:("science fiction")', "downloads desc"),
    ("Poetry", "subject:poetry", "downloads desc"),
    ("Philosophy & Ideas", "subject:(philosophy OR essays)", "downloads desc"),
    ("For Young Listeners", "subject:(children OR juvenile OR fairy)", "downloads desc"),
)

LANGUAGES = (
    ("Any language", ""),
    ("English", "eng OR English OR en"),
    ("Spanish", "spa OR Spanish OR es"),
    ("French", "fre OR fra OR French OR fr"),
    ("German", "ger OR deu OR German OR de"),
    ("Italian", "ita OR Italian OR it"),
    ("Portuguese", "por OR Portuguese OR pt"),
    ("Latin", "lat OR Latin OR la"),
    ("Russian", "rus OR Russian OR ru"),
    ("Dutch", "dut OR nld OR Dutch OR nl"),
    ("Swedish", "swe OR Swedish OR sv"),
    ("Polish", "pol OR Polish OR pl"),
    ("Greek", "gre OR ell OR grc OR Greek"),
    ("Hebrew", "heb OR Hebrew OR he"),
    ("Arabic", "ara OR Arabic OR ar"),
    ("Persian", "per OR fas OR Persian OR fa"),
    ("Turkish", "tur OR Turkish OR tr"),
    ("Hindi", "hin OR Hindi OR hi"),
    ("Bengali", "ben OR Bengali OR bn"),
    ("Tamil", "tam OR Tamil OR ta"),
    ("Sanskrit", "san OR Sanskrit OR sa"),
    ("Chinese", "chi OR zho OR Chinese OR zh"),
    ("Japanese", "jpn OR Japanese OR ja"),
    ("Korean", "kor OR Korean OR ko"),
)


def _period(low: int, high: int) -> str:
    return f"date:[{low:04d}-01-01 TO {high:04d}-12-31]"


BROWSE_SUBJECTS = (
    ("Fiction", 'subject:(fiction OR novel OR novels OR "short stories")'),
    ("Science Fiction", 'subject:("science fiction")'),
    ("Fantasy", "subject:(fantasy)"),
    ("Mystery & Detective", 'subject:("detective and mystery" OR "mystery fiction" OR detective)'),
    ("Horror & Ghost Stories", 'subject:("horror tales" OR "ghost stories" OR horror OR "gothic fiction")'),
    ("Romance", 'subject:("love stories" OR romance OR "romance fiction")'),
    ("Adventure", 'subject:("adventure stories" OR "sea stories" OR adventure)'),
    ("Poetry", "subject:(poetry OR poems)"),
    ("Drama & Plays", "subject:(drama OR plays OR theater OR theatre)"),
    ("Children's Books", 'subject:("children\'s stories" OR "juvenile fiction" OR "juvenile literature" OR "fairy tales")'),
    ("Myth & Folklore", "subject:(mythology OR folklore OR legends)"),
    ("History", "subject:history"),
    ("Biography & Memoir", "subject:(biography OR autobiography OR memoirs)"),
    ("Philosophy", "subject:(philosophy OR ethics)"),
    ("Religion & Spirituality", "subject:(religion OR theology OR bible OR spirituality)"),
    ("Psychology", "subject:(psychology OR psychoanalysis)"),
    ("Politics", 'subject:(politics OR "political science" OR government)'),
    ("Law", "subject:(law OR legal)"),
    ("Economics & Business", "subject:(economics OR business OR finance)"),
    ("Education", "subject:(education OR teaching)"),
    ("Science", "subject:(science OR physics OR chemistry OR astronomy)"),
    ("Mathematics", "subject:(mathematics OR algebra OR geometry OR calculus)"),
    ("Nature & Animals", 'subject:("natural history" OR botany OR zoology OR birds OR animals)'),
    ("Medicine & Health", "subject:(medicine OR health OR nursing OR anatomy)"),
    ("Technology", "subject:(technology OR engineering OR electronics)"),
    ("Computers", "subject:(computers OR programming OR software)"),
    ("Art", "subject:(art OR painting OR drawing OR sculpture)"),
    ("Architecture & Design", "subject:(architecture OR design OR furniture)"),
    ("Music", "subject:(music OR songs OR hymns)"),
    ("Travel & Places", "subject:(travel OR voyages OR description)"),
    ("Food & Cooking", "subject:(cooking OR cookery OR recipes)"),
    ("Home & Garden", "subject:(gardening OR agriculture OR crafts OR needlework)"),
    ("Sports & Games", "subject:(sports OR games OR chess)"),
    ("Comics", "collection:comics"),
    ("Magazines", "collection:(pulpmagazinearchive OR magazine_rack)"),
)

BROWSE_PERIODS = (
    ("Before 1500", _period(1, 1499)), ("1500s", _period(1500, 1599)), ("1600s", _period(1600, 1699)),
    ("1700s", _period(1700, 1799)), ("Early 1800s", _period(1800, 1849)), ("Late 1800s", _period(1850, 1899)),
    ("1900s", _period(1900, 1909)), ("1910s", _period(1910, 1919)), ("1920s", _period(1920, 1929)),
    ("1930s", _period(1930, 1939)), ("1940s", _period(1940, 1949)), ("1950s", _period(1950, 1959)),
    ("1960s", _period(1960, 1969)), ("1970s", _period(1970, 1979)), ("1980s", _period(1980, 1989)),
    ("1990s", _period(1990, 1999)), ("2000s", _period(2000, 2009)), ("Since 2010", _period(2010, 2100)),
)

# (name, Archive collection, what's in it)
BROWSE_COLLECTIONS = (
    ("Project Gutenberg", "gutenberg", "Classics, carefully proofread by volunteers"),
    ("American Libraries", "americana", "Scanned from libraries across the United States"),
    ("Canadian Libraries", "toronto", "Scanned from the University of Toronto and others"),
    ("European Libraries", "europeanlibraries", "Scanned from libraries across Europe"),
    ("California Digital Library", "cdl", "From the University of California"),
    ("Library of Congress", "library_of_congress", "From the national library of the United States"),
    ("New York Public Library", "newyorkpubliclibrary", "From one of the world's great public libraries"),
    ("Smithsonian", "smithsonian", "Science, history and culture from the Smithsonian Libraries"),
    ("Biodiversity Heritage", "biodiversity", "Centuries of natural history, richly illustrated"),
    ("Medical Heritage", "medicallibrary", "The history of medicine and health"),
    ("Pulp Magazines", "pulpmagazinearchive", "Science fiction, crime and adventure pulps"),
    ("Comic Books", "comics", "Comic books scanned by the community"),
    ("Community Texts", "opensource", "Uploaded by people around the world"),
    ("Folkscanomy", "folkscanomy", "Books gathered and organized by subject"),
)

SORTS = (
    ("Most popular", "downloads desc"),
    ("Trending", "week desc"),
    ("Recently added", "addeddate desc"),
    ("Title", "titleSorter asc"),
    ("Oldest first", "date asc"),
    ("Newest first", "date desc"),
)


def _words(text: str) -> list[str]:
    """Split user input into safely quotable words; Lucene syntax never gets through."""
    return [w.lower() for w in re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text, re.UNICODE)][:12]


def _all_of(text: str) -> str:
    return " AND ".join(f'"{w}"' for w in _words(text))


def user_query(text: str) -> str:
    """Match a free-text search against title, author and subject."""
    terms = _all_of(text)
    if not terms:
        return ""
    return f"(title:({terms})^4 OR creator:({terms})^3 OR subject:({terms}))"


def search_query(
    text: str = "",
    kind: str = "text",
    language: str = "",
    subject: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    base: str = "",
) -> str:
    """Compose a search query. `base` narrows it to a category instead of the whole catalog."""
    if base:
        pass
    elif kind == "audio":
        base = FREE_AUDIO
    elif kind == "any":
        base = f"(({ALL_TEXTS}) OR ({FREE_AUDIO}))"
    else:
        base = ALL_TEXTS
    parts = [base]
    typed = user_query(text)
    if typed:
        parts.append(typed)
    if language:
        parts.append(f"language:({language})")
    if subject and (terms := _all_of(subject)):
        parts.append(f"subject:({terms})")
    if year_from is not None or year_to is not None:
        low = f"{year_from:04d}" if year_from is not None else "0001"
        high = f"{year_to:04d}" if year_to is not None else "2100"
        parts.append(f"date:[{low}-01-01 TO {high}-12-31]")
    return " AND ".join(parts)


def shelf_query(fragment: str, kind: str = "text") -> str:
    parts = [FREE_AUDIO if kind == "audio" else FREE_TEXTS, _ENGLISH]
    if fragment:
        parts.append(f"({fragment})")
    return " AND ".join(parts) + " " + _SHELF_CLEAN


def browse_query(fragment: str) -> str:
    return f"{ALL_TEXTS} AND ({fragment}) NOT subject:(erotic OR erotica)"


def browse_tiles() -> dict[str, tuple[tuple[str, str, str], ...]]:
    """Each Browse section as (name, query, description) entries."""
    return {
        "Subjects": tuple((n, browse_query(f), "") for n, f in BROWSE_SUBJECTS),
        "Through the Centuries": tuple((n, browse_query(f), "") for n, f in BROWSE_PERIODS),
        "Languages": tuple((n, browse_query(f"language:({v})"), "") for n, v in LANGUAGES if v),
        "Great Libraries and Collections": tuple(
            (n, browse_query(f"collection:{c}"), tip) for n, c, tip in BROWSE_COLLECTIONS),
    }


def browse_count_queries() -> list[str]:
    return [ALL_TEXTS] + [entry[1] for section in browse_tiles().values() for entry in section]


def count_key(query: str) -> str:
    return hashlib.sha1(query.encode()).hexdigest()[:16]


def set_adult_filter(enabled: bool) -> None:
    global _adult_filter
    _adult_filter = enabled


def search_url(query: str, sort: str = "downloads desc", rows: int = 40, page: int = 1) -> str:
    """Every catalog request is built here, so the adult filter can't be missed by a screen."""
    if _adult_filter:
        query = f"({query}) AND {_NOT_ADULT}"
    params: list[tuple[str, str]] = [("q", query)]
    params += [("fl[]", f) for f in FIELDS]
    params += [("sort[]", sort), ("rows", str(rows)), ("page", str(page)), ("output", "json")]
    return f"{SEARCH_URL}?{urlencode(params)}"


def metadata_url(identifier: str) -> str:
    return f"{BASE}/metadata/{quote(identifier, safe='')}"


def cover_url(identifier: str) -> str:
    return f"{BASE}/services/img/{quote(identifier, safe='')}"


def file_url(identifier: str, name: str) -> str:
    return f"{BASE}/download/{quote(identifier, safe='')}/{quote(name)}"


def page_image_url(identifier: str, index: int, width: int) -> str:
    return f"{BASE}/download/{quote(identifier, safe='')}/page/n{index}_w{width}.jpg"


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _clean_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_author(creator: Any) -> str:
    """Turn 'Baum, L. Frank (Lyman Frank), 1856-1919' into 'L. Frank Baum'."""
    names = _as_list(creator)
    if not names:
        return "Unknown author"
    raw = _clean_str(names[0])
    raw = re.sub(r"\([^)]*\)", "", raw)
    raw = re.sub(r",?\s*\b(?:b\.|d\.|fl\.|ca\.)?\s*\d{3,4}\??\s*-?\s*(?:\d{3,4})?\??\.?", "", raw)
    raw = re.sub(r",\s*(?:ill|illus|ed|tr|trans|comp|Sir|Bp|Emperor[^,]*)\.?\s*(?=,|$)", "", raw)
    raw = raw.strip(" ,.;")
    pieces = [p.strip() for p in raw.split(",") if p.strip()]
    if len(pieces) >= 2 and len(pieces[0].split()) <= 3:
        return f"{pieces[1]} {pieces[0]}"
    return pieces[0] if pieces else "Unknown author"


def clean_title(value: Any) -> str:
    items = _as_list(value)
    title = _clean_str(items[0]) if items else "Untitled"
    title = re.sub(r"\s*\[electronic resource\]\s*", " ", title, flags=re.I).strip()
    return title[:1].upper() + title[1:] if title else "Untitled"


def _year(doc: dict) -> str:
    for key in ("year", "date"):
        match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(doc.get(key) or ""))
        if match:
            return match.group(1)
    return ""


def clean_subjects(value: Any) -> list[str]:
    """Normalize subject headings, dropping those too generic to recommend from."""
    generic = {
        "fiction", "literature", "english", "novel", "novels", "book", "books", "librivox",
        "audiobook", "audiobooks", "english fiction", "english literature", "american fiction",
        "american literature", "stories", "general", "text", "bookreader", "reading", "family",
        "courtship", "love", "life", "history", "biography", "women", "men", "marriage", "people",
    }
    out: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        for part in re.split(r"--|;", str(item)):
            subject = _clean_str(part).strip(" .,")
            key = subject.lower()
            if len(subject) < 3 or key in generic or key in seen or re.search(r"\d{3}", key):
                continue
            seen.add(key)
            out.append(subject)
    return out[:16]


def book_from_doc(doc: dict) -> dict:
    """A compact book record from one search result."""
    kind = "audio" if doc.get("mediatype") == "audio" else "text"
    return {
        "id": doc["identifier"],
        "title": clean_title(doc.get("title")),
        "author": clean_author(doc.get("creator")),
        "kind": kind,
        "year": _year(doc),
        "language": _clean_str(_as_list(doc.get("language"))[0]) if doc.get("language") else "",
        "subjects": clean_subjects(doc.get("subject")),
        "downloads": int(doc.get("downloads") or 0),
        "collections": [str(c) for c in _as_list(doc.get("collection"))],
        "source": "ia",
    }


def _edition_key(book: dict) -> str:
    title = re.split(r"[:;]|,?\s+or[,\s]", book["title"].lower())[0]
    title = re.sub(r"^(the|a|an)\s+", "", re.sub(r"[^\w\s]", "", title)).strip()
    surname = (book["author"].split() or [""])[-1].lower()
    return f"{title}|{surname}"


def dedupe_editions(books: list[dict]) -> list[dict]:
    """Keep one edition per work. Gutenberg editions win because their text is clean."""
    best: dict[str, int] = {}
    out: list[dict] = []
    for book in books:
        key = _edition_key(book)
        if key not in best:
            best[key] = len(out)
            out.append(book)
        elif "gutenberg" in book.get("collections", []) and (
            "gutenberg" not in out[best[key]].get("collections", [])
        ):
            out[best[key]] = book
    return out


def parse_search(payload: dict) -> tuple[list[dict], int]:
    response = payload.get("response") or {}
    docs = response.get("docs") or []
    return [book_from_doc(d) for d in docs if d.get("identifier")], int(response.get("numFound") or 0)


def parse_duration(value: Any) -> float:
    """Audio lengths appear as seconds ('507.1') or clock time ('08:26', '1:02:03')."""
    text = str(value or "").strip()
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _is_private(file: dict) -> bool:
    return str(file.get("private", "")).lower() == "true"


def _natural(name: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def _pick_epub(files: list[dict]) -> dict | None:
    epubs = [
        f for f in files
        if f["name"].lower().endswith(".epub")
        and "_lcp" not in f["name"].lower()
        and "encrypted" not in f["name"].lower()
    ]
    if not epubs:
        return None
    epubs.sort(key=lambda f: (f.get("source") != "original", "-images" not in f["name"]))
    best = epubs[0]
    return {"name": best["name"], "size": int(best.get("size") or 0),
            "original": best.get("source") == "original"}


def _pick_pdf(files: list[dict]) -> dict | None:
    pdfs = [
        f for f in files
        if f["name"].lower().endswith(".pdf") and "encrypted" not in f["name"].lower()
        and "encrypted" not in str(f.get("format", "")).lower()
    ]
    if not pdfs:
        return None
    pdfs.sort(key=lambda f: (f["name"].lower().endswith("_bw.pdf"), f.get("format") != "Text PDF"))
    return {"name": pdfs[0]["name"], "size": int(pdfs[0].get("size") or 0)}


def _pick_text(files: list[dict]) -> dict | None:
    djvu = [f for f in files if f.get("format") == "DjVuTXT"]
    if djvu:
        return {"name": djvu[0]["name"], "size": int(djvu[0].get("size") or 0), "form": "djvu"}
    plain = [f for f in files if f.get("format") == "Text" and f["name"].lower().endswith(".txt")]
    if not plain:
        return None
    # Gutenberg's "-0.txt" is UTF-8; "-8.txt" is Latin-1.
    plain.sort(key=lambda f: ("-0." not in f["name"], "-8." in f["name"]))
    return {"name": plain[0]["name"], "size": int(plain[0].get("size") or 0), "form": "plain"}


def _pick_tracks(files: list[dict]) -> list[dict]:
    audio = [f for f in files if f["name"].lower().endswith((".mp3", ".ogg"))
             and "_sample" not in f["name"].lower()]
    chosen: list[dict] = []
    for fmt in ("64Kbps MP3", "VBR MP3", "128Kbps MP3", "MP3", "Ogg Vorbis"):
        chosen = [f for f in audio if f.get("format") == fmt]
        if chosen:
            break
    by_name = {f["name"]: f for f in files}

    def order(file: dict) -> tuple:
        original = by_name.get(str(file.get("original") or ""), {})
        match = re.match(r"\d+", str(file.get("track") or original.get("track") or ""))
        return (int(match.group()) if match else 10**6, _natural(file["name"]))

    tracks = []
    for index, file in enumerate(sorted(chosen, key=order), start=1):
        title = _clean_str(file.get("title") or "") or f"Chapter {index}"
        tracks.append({
            "name": file["name"],
            "title": title,
            "seconds": parse_duration(file.get("length")),
            "size": int(file.get("size") or 0),
        })
    return tracks


def _description(meta: dict) -> str:
    raw = " ".join(dict.fromkeys(str(d).strip() for d in _as_list(meta.get("description"))))
    raw = re.sub(r"(?i)<br\s*/?>|</p>", "\n\n", raw)
    raw = html.unescape(re.sub(r"<[^>]+>", "", raw))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", raw)).strip()


def analyze_item(payload: dict) -> dict:
    """Work out what can actually be opened for an item, and how best to open it.

    Smart pick: a born-digital EPUB gives the best reading, so use it when there
    is one. If the EPUB was machine-generated from scans its text can be garbled,
    so the page scans win and the text stays available behind the toggle.
    """
    meta = payload.get("metadata") or {}
    identifier = str(meta.get("identifier") or "")
    media = meta.get("mediatype")
    files = [f for f in (payload.get("files") or []) if f.get("name") and not _is_private(f)]
    restricted = str(meta.get("access-restricted-item", "")).lower() == "true"

    plan: dict[str, Any] = {
        "id": identifier,
        "server": str(payload.get("server") or ""),
        "dir": str(payload.get("dir") or ""),
        "title": clean_title(meta.get("title")),
        "author": clean_author(meta.get("creator")),
        "kind": "audio" if media == "audio" else "text",
        "description": _description(meta),
        "subjects": clean_subjects(meta.get("subject")),
        "language": _clean_str(_as_list(meta.get("language"))[0]) if meta.get("language") else "",
        "year": _year(meta),
        "publisher": _clean_str(_as_list(meta.get("publisher"))[0]) if meta.get("publisher") else "",
        "epub": None, "pdf": None, "text": None, "pages": 0, "tracks": [],
        "views": [], "default_view": None, "available": False, "restricted": restricted,
    }
    if restricted:
        return plan

    if plan["kind"] == "audio":
        plan["tracks"] = _pick_tracks(files)
        plan["available"] = bool(plan["tracks"])
        return plan

    plan["epub"] = _pick_epub(files)
    plan["pdf"] = _pick_pdf(files)
    plan["text"] = _pick_text(files)
    plan["ocr_xml"] = next(
        (f["name"] for f in files if str(f.get("format", "")).lower() == "djvu xml"), None)
    has_scan_source = any(re.search(r"_(jp2|tif)\.(zip|tar)$|_raw_jpg\.tar$", f["name"]) for f in files)
    if has_scan_source:
        try:
            plan["pages"] = int(meta.get("imagecount") or 0)
        except (TypeError, ValueError):
            plan["pages"] = 0

    has_text = bool(plan["epub"] or plan["text"])
    has_scan = bool(plan["pages"] or plan["pdf"])
    plan["available"] = has_text or has_scan
    # Each book gets the one form that reads best. Text machine-read from a scan is too
    # error-prone to offer when the scan itself exists.
    if plan["epub"] and plan["epub"]["original"]:
        plan["default_view"] = "text"
    elif has_scan:
        plan["default_view"] = "scan"
    elif has_text:
        plan["default_view"] = "text"
    plan["views"] = [plan["default_view"]] if plan["default_view"] else []
    return plan


def recommendation_seeds(books: list[dict], limit: int = 3) -> list[dict]:
    """Pick recently read books with enough metadata to recommend from."""
    seeds = []
    for book in books:
        if book.get("source") != "ia" or not (book.get("subjects") or book.get("author")):
            continue
        seeds.append(book)
        if len(seeds) == limit:
            break
    return seeds


def recommendation_fragment(seed: dict, library: list[dict]) -> str:
    """A shelf query for books like the seed, weighting subjects common in the library."""
    weights: Counter[str] = Counter()
    for book in library:
        weights.update(book.get("subjects") or [])
    subjects = sorted(seed.get("subjects") or [], key=lambda s: -weights[s])[:4]
    clauses = []
    if subjects:
        joined = " OR ".join('"' + re.sub(r'["\\]', " ", s) + '"' for s in subjects)
        clauses.append(f"subject:({joined})")
    author = seed.get("author") or ""
    if author and author != "Unknown author":
        surname = _all_of(author.split()[-1])
        if surname:
            clauses.append(f"creator:({surname})")
    return " OR ".join(clauses)
