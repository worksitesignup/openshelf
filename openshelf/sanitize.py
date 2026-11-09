"""Make book files safe to display.

EPUBs are zip files of web pages, so a downloaded book could contain scripts or
reach out to the network. Nothing from a book is shown until it has passed through
this allow-list: only known-harmless tags and attributes get out, and resource and
link addresses are rewritten by the caller.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import Callable

ALLOWED_TAGS = {
    "p", "div", "span", "a", "img", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "dl", "dt", "dd", "blockquote", "pre", "code", "em", "strong", "b", "i", "u", "s", "sub",
    "sup", "small", "big", "br", "hr", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "caption", "colgroup", "col", "figure", "figcaption", "section", "article", "aside",
    "header", "footer", "center", "cite", "q", "abbr", "dfn", "kbd", "samp", "var", "ruby",
    "rt", "rp", "tt", "nav", "main", "address", "del", "ins", "wbr",
}
VOID_TAGS = {"img", "br", "hr", "col", "wbr"}
# These are removed along with everything inside them.
DROP_WITH_CONTENT = {
    "head", "script", "style", "iframe", "object", "embed", "applet", "audio", "video",
    "form", "input", "button", "select", "textarea", "noscript", "template", "canvas",
    "link", "meta", "base", "title", "frameset", "frame", "math",
}
_VOID_DROPS = {"link", "meta", "base", "input", "embed", "frame"}
ALLOWED_ATTRS = {"class", "id", "title", "lang", "dir", "alt", "colspan", "rowspan", "style"}

_STYLE_PROPS = {
    "text-align", "font-style", "font-weight", "font-variant", "text-indent", "text-decoration",
    "vertical-align", "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
    "padding", "padding-left", "padding-right", "padding-top", "padding-bottom", "text-transform",
    "letter-spacing", "white-space", "float", "clear", "display", "width", "max-width",
    "list-style-type", "border", "border-top", "border-bottom", "border-left", "border-right",
}


def clean_inline_style(style: str) -> str:
    kept = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if prop in _STYLE_PROPS and not re.search(r"(?i)url\(|expression|javascript|[<>\\]", value):
            kept.append(f"{prop}:{value}")
    return ";".join(kept)


_REMOVED_CSS_PROPS = (
    r"position|top|bottom|overflow(?:-[xy])?|height|min-height|max-height|z-index|"
    r"(?:-[a-z]+-)?columns?(?:-[a-z]+)*|font-family|line-height|color|background(?:-[a-z]+)*|"
    r"(?:-[a-z]+-)?transform(?:-[a-z]+)*|transition(?:-[a-z]+)*|animation(?:-[a-z-]+)*|"
    r"behavior|-moz-binding|filter"
)


def clean_css(css: str) -> str:
    """Remove anything that could load remote content or fight the reader's layout."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"(?is)@(?:font-face|page|keyframes|-webkit-keyframes)[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css)
    css = re.sub(r"(?i)@(?:import|charset|namespace)[^;]*;", "", css)
    css = re.sub(r"(?i)url\([^)]*\)", "none", css)
    css = re.sub(rf"(?i)(?<![\w-])(?:{_REMOVED_CSS_PROPS})\s*:[^;{{}}]*;?", "", css)
    css = re.sub(r"(?i)font-size\s*:\s*[\d.]+(?:px|pt)\s*;?", "", css)
    css = css.replace("<", "").replace("\\", "")
    depth = 0
    for char in css:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            break
    if depth != 0:
        return ""  # Unbalanced braces could escape the wrapper rule, so use none of it.
    return css


class _Sanitizer(HTMLParser):
    def __init__(
        self,
        resolve_image: Callable[[str], str | None],
        resolve_link: Callable[[str], dict[str, str]],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.resolve_image = resolve_image
        self.resolve_link = resolve_link
        self.out: list[str] = []
        self.text: list[str] = []
        self.styles: list[str] = []
        self.skip_depth = 0
        self.skip_tag = ""

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts = []
        values = {k.lower(): (v or "") for k, v in attrs}
        for key in ALLOWED_ATTRS:
            value = values.get(key)
            if value is None:
                continue
            if key == "style":
                value = clean_inline_style(value)
                if not value:
                    continue
            parts.append(f' {key}="{escape(value, quote=True)}"')
        if tag == "img":
            source = values.get("src") or values.get("xlink:href") or values.get("href") or ""
            url = self.resolve_image(source) if source else None
            if url:
                parts.append(f' src="{escape(url, quote=True)}"')
        elif tag == "a":
            href = values.get("href") or values.get("xlink:href")
            if href:
                for key, value in self.resolve_link(href).items():
                    parts.append(f' {key}="{escape(value, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag == self.skip_tag and tag not in _VOID_DROPS:
                self.skip_depth += 1
            return
        if tag == "link":
            values = {k.lower(): (v or "") for k, v in attrs}
            if "stylesheet" in values.get("rel", "").lower() and values.get("href"):
                self.styles.append(values["href"])
            return
        if tag in _VOID_DROPS:
            return
        if tag in DROP_WITH_CONTENT:
            # <head> is dropped, but its stylesheet links are still wanted.
            if tag != "head":
                self.skip_tag, self.skip_depth = tag, 1
            return
        if tag == "image":  # <svg><image/></svg> is how many EPUBs embed cover art.
            tag = "img"
        if tag in ALLOWED_TAGS:
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        lowered = tag.lower()
        if lowered not in VOID_TAGS and lowered != "image":
            self.handle_endtag(lowered)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth -= 1
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skip_depth or self.cdata_elem:
            return
        self.out.append(escape(data, quote=False))
        self.text.append(data)

    def handle_comment(self, data: str) -> None:
        pass

    def handle_decl(self, decl: str) -> None:
        pass

    def handle_pi(self, data: str) -> None:
        pass

    def unknown_decl(self, data: str) -> None:
        pass


def sanitize_html(
    source: str,
    resolve_image: Callable[[str], str | None] = lambda _src: None,
    resolve_link: Callable[[str], dict[str, str]] = lambda _href: {},
) -> tuple[str, str, list[str]]:
    """Return (safe html, plain text, stylesheet hrefs) for one chapter."""
    parser = _Sanitizer(resolve_image, resolve_link)
    source = re.sub(r"^\s*<\?xml[^>]*\?>", "", source)
    parser.feed(source)
    parser.close()
    html_out = "".join(parser.out).replace("<body>", "").replace("</body>", "")
    return html_out, "".join(parser.text), parser.styles
