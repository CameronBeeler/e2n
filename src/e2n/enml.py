"""ENML content planning helpers for Notion block conversion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from e2n.enex import EVERNOTE_LINK_PATTERN, EVERNOTE_WEB_LINK_PATTERN, _local_name


DEFAULT_TEXT_BLOCK_LIMIT = 1800

# Tags that represent standalone non-text media objects requiring their own block.
NON_TEXT_TAGS = {"en-media", "object", "iframe", "embed", "audio", "video"}

# Heading tag names mapped to Notion heading levels (h4-h6 collapse to 3).
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 3, "h5": 3, "h6": 3}

# Tags that produce list-item segments.
LIST_CONTAINER_TAGS = {"ul", "ol"}

# Inline formatting tags mapped to annotation keys.
FORMATTING_TAGS = {
    "b": "bold", "strong": "bold",
    "i": "italic", "em": "italic",
    "u": "underline",
    "s": "strikethrough", "strike": "strikethrough", "del": "strikethrough",
    "code": "code",
}


@dataclass
class ContentSegment:
    """A planned conversion segment split around non-text ENML content.

    kind values:
      text          — plain text run (may contain inline HTTP link spans after merging)
      http_link     — an <a href="http(s)://..."> anchor; emitted inline inside a paragraph
      evernote_link — an <a href="evernote://..."> internal note link; deferred resolution
      resource      — an <en-media> or similar binary attachment
      table         — an HTML table with row/cell data
      heading       — a heading element (h1-h6)
      bulleted_list — an unordered list item
      numbered_list — an ordered list item
      quote         — a blockquote
      code          — a preformatted/code block
      divider       — a horizontal rule
      to_do         — a checkbox item
      encrypted     — an en-crypt block (unresolvable without passphrase)
    """

    kind: Literal[
        "text", "http_link", "evernote_link", "resource", "table",
        "heading", "bulleted_list", "numbered_list", "quote", "code",
        "divider", "to_do", "encrypted",
    ]
    text: str
    value: str = ""
    mime_type: str = ""
    level: int = 0
    checked: bool = False
    rows: list | None = None
    annotations: dict | None = None
    inline: bool = False


def plan_enml_segments(content: str, text_block_limit: int = DEFAULT_TEXT_BLOCK_LIMIT) -> tuple[ContentSegment, ...]:
    """Split ENML into text chunks and standalone non-text segments.

    Embedded Evernote links and resources are emitted as their own segments. Text
    before and after those segments is preserved as separate text segments.
    """
    if not content.strip():
        return ()
    try:
        root = etree.fromstring(content.encode("utf-8"), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return _text_segments(content, text_block_limit)

    segments: list[ContentSegment] = []
    _walk_enml(root, segments, text_block_limit)
    return tuple(segment for segment in segments if segment.text or segment.value or segment.kind == "divider" or (segment.kind == "text" and not segment.inline))


def _is_evernote_link(href: str) -> bool:
    """Return whether a URL is any form of Evernote internal link."""
    return bool(EVERNOTE_LINK_PATTERN.match(href) or EVERNOTE_WEB_LINK_PATTERN.match(href))


def _walk_enml(element: etree._Element, segments: list[ContentSegment], text_block_limit: int) -> None:
    if element.text:
        segments.extend(_text_segments(element.text, text_block_limit, inline=True))

    for child in element:
        href = child.attrib.get("href", "")
        tag_name = _local_name(child.tag)

        if tag_name == "a" and _is_evernote_link(href):
            link_text = " ".join("".join(child.itertext()).split()) or href
            segments.append(ContentSegment(kind="evernote_link", text=link_text, value=href))
        elif tag_name == "a" and href:
            link_text = " ".join("".join(child.itertext()).split()) or href
            segments.append(ContentSegment(kind="http_link", text=link_text, value=href))
        elif tag_name in HEADING_TAGS:
            text = " ".join("".join(child.itertext()).split())
            if text:
                segments.append(ContentSegment(kind="heading", text=text, level=HEADING_TAGS[tag_name]))
        elif tag_name == "ul":
            _walk_list(child, segments, "bulleted_list", text_block_limit)
        elif tag_name == "ol":
            _walk_list(child, segments, "numbered_list", text_block_limit)
        elif tag_name == "blockquote":
            text = " ".join("".join(child.itertext()).split())
            if text:
                segments.append(ContentSegment(kind="quote", text=text))
        elif tag_name == "pre":
            text = "".join(child.itertext())
            if text:
                segments.append(ContentSegment(kind="code", text=text))
        elif tag_name == "hr":
            segments.append(ContentSegment(kind="divider", text=""))
        elif tag_name == "en-todo":
            checked = child.attrib.get("checked", "false").lower() == "true"
            # Text follows as tail of en-todo element
            todo_text = " ".join((child.tail or "").split())
            if todo_text:
                segments.append(ContentSegment(kind="to_do", text=todo_text, checked=checked))
                # Clear tail so parent doesn't re-emit it
                child.tail = None
        elif tag_name == "en-crypt":
            hint = child.attrib.get("hint", "")
            segments.append(ContentSegment(kind="encrypted", text="en-crypt", value=hint))
        elif tag_name == "table":
            rows = _parse_table(child)
            segments.append(ContentSegment(kind="table", text="table", rows=rows))
        elif tag_name in NON_TEXT_TAGS:
            mime = child.attrib.get("type", "")
            segments.append(ContentSegment(kind="resource", text=tag_name, value=_resource_value(child), mime_type=mime))
        elif tag_name in FORMATTING_TAGS:
            _walk_formatted(child, segments, text_block_limit, {FORMATTING_TAGS[tag_name]: True})
        elif tag_name in ("div", "p"):
            # Block-level boundary: flush previous content, then recurse
            segments.append(ContentSegment(kind="text", text="", inline=False))
            _walk_enml(child, segments, text_block_limit)
        elif tag_name == "br":
            # BR forces a new non-inline segment
            segments.append(ContentSegment(kind="text", text="", inline=False))
        else:
            _walk_enml(child, segments, text_block_limit)

        if child.tail:
            segments.extend(_text_segments(child.tail, text_block_limit, inline=True))


def _walk_list(element: etree._Element, segments: list[ContentSegment], kind: str, text_block_limit: int) -> None:
    """Extract list items from a ul/ol element."""
    for child in element:
        if _local_name(child.tag) == "li":
            text = " ".join("".join(child.itertext()).split())
            if text:
                segments.append(ContentSegment(kind=kind, text=text))


def _walk_formatted(
    element: etree._Element,
    segments: list[ContentSegment],
    text_block_limit: int,
    annotations: dict,
) -> None:
    """Walk an inline formatting element, emitting annotated text segments."""
    if element.text:
        text = " ".join(element.text.split())
        if text:
            segments.append(ContentSegment(kind="text", text=text, annotations=dict(annotations)))

    for child in element:
        tag_name = _local_name(child.tag)
        if tag_name in FORMATTING_TAGS:
            merged = {**annotations, FORMATTING_TAGS[tag_name]: True}
            _walk_formatted(child, segments, text_block_limit, merged)
        else:
            _walk_enml(child, segments, text_block_limit)

        if child.tail:
            tail_text = " ".join(child.tail.split())
            if tail_text:
                segments.append(ContentSegment(kind="text", text=tail_text, annotations=dict(annotations)))


def _parse_table(element: etree._Element) -> list[list[str]]:
    """Extract table rows as lists of cell text."""
    rows: list[list[str]] = []
    for descendant in element.iter():
        if _local_name(descendant.tag) == "tr":
            cells: list[str] = []
            for cell in descendant:
                if _local_name(cell.tag) in ("td", "th"):
                    cells.append(" ".join("".join(cell.itertext()).split()))
            if cells:
                rows.append(cells)
    return rows


def _text_segments(text: str, text_block_limit: int, inline: bool = False) -> tuple[ContentSegment, ...]:
    # Split on line breaks to preserve paragraph structure
    lines = text.split("\n")
    segments: list[ContentSegment] = []
    for line in lines:
        normalized = " ".join(line.split())
        if normalized:
            segments.extend(
                ContentSegment(kind="text", text=chunk, inline=inline)
                for chunk in chunk_text(normalized, text_block_limit)
            )
    return tuple(segments)


def chunk_text(text: str, limit: int = DEFAULT_TEXT_BLOCK_LIMIT) -> tuple[str, ...]:
    """Break text into chunks that fit Notion block-size constraints."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(word) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(word[start : start + limit] for start in range(0, len(word), limit))
            continue
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return tuple(chunks)


def _resource_value(element: etree._Element) -> str:
    return element.attrib.get("hash") or element.attrib.get("src") or etree.tostring(element, encoding="unicode")
