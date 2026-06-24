"""Notion API helpers for migration workspace setup."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from e2n.exceptions import EvernoteEmbeddedLinkRecord, UnsupportedContentRecord


DEFAULT_CONVERTED_PAGE_TITLE = "Evernote Import"
DEFAULT_EXCEPTIONS_PAGE_TITLE = "Evernote Import Exceptions"
DEFAULT_EXCEPTIONS_DATABASE_TITLE = "Import-Exceptions"
EXCEPTION_REASON_PROPERTY = "Reason"
EXCEPTION_KEY_PROPERTY = "Exception Key"
EXCEPTION_STATUS_PROPERTY = "Status"
IMPORT_TAGS_PROPERTY = "Tags"

# ---------------------------------------------------------------------------
# MIME type registry (REQ-BLOCK-03, REQ-UNSUPPORTED-01)
# ---------------------------------------------------------------------------

# MIME type prefixes that the Notion API cannot represent as displayable blocks.
# Audio and video require an external streaming URL — Evernote stores these as
# base64 blobs, so they cannot be directly uploaded to a Notion audio/video block.
# Spreadsheets and presentations have no Notion block equivalent at all.
UNSUPPORTED_MIME_PREFIXES: frozenset[str] = frozenset({
    "audio/",
    "video/",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml",
})

JsonObject = dict[str, Any]


def mime_to_notion_block_type(mime: str) -> Literal["image", "pdf", "file", "unsupported"]:
    """Map an attachment MIME type to its Notion block representation.

    Returns:
        "image"       — Notion image block
        "pdf"         — Notion pdf block
        "file"        — Notion file block (generic attachment)
        "unsupported" — No Notion block can represent this type; use a callout placeholder
    """
    mime_lower = mime.lower().strip()
    if mime_lower.startswith("image/"):
        return "image"
    if mime_lower == "application/pdf":
        return "pdf"
    for prefix in UNSUPPORTED_MIME_PREFIXES:
        if mime_lower.startswith(prefix):
            return "unsupported"
    return "file"


# ---------------------------------------------------------------------------
# Notion block JSON builders (REQ-BLOCK-03, REQ-LINK-01)
# ---------------------------------------------------------------------------

def plain_text_span(text: str) -> JsonObject:
    """Build a Notion rich_text element for a plain text run."""
    return {"type": "text", "text": {"content": text}}


def link_text_span(text: str, url: str) -> JsonObject:
    """Build a Notion rich_text element carrying an inline link annotation."""
    # Validate URL — Notion only accepts http(s) and mailto
    if url and (url.startswith("http://") or url.startswith("https://") or url.startswith("mailto:")):
        return {"type": "text", "text": {"content": text, "link": {"url": url}}}
    # Invalid, relative, or protocol-less URL — render as plain text with URL shown
    if url and url != text:
        return {"type": "text", "text": {"content": f"{text} [{url}]"}}
    return {"type": "text", "text": {"content": text}}


def paragraph_block(rich_text: list[JsonObject]) -> JsonObject:
    """Build a Notion paragraph block from a list of rich_text spans."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text}}


def image_block(url: str) -> JsonObject:
    """Build a Notion image block referencing an external or uploaded URL."""
    return {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}}


def image_block_upload(upload_id: str) -> JsonObject:
    """Build a Notion image block from a file upload ID."""
    return {"object": "block", "type": "image", "image": {"type": "file_upload", "file_upload": {"id": upload_id}}}


def pdf_block(url: str) -> JsonObject:
    """Build a Notion pdf block referencing an external or uploaded URL."""
    return {"object": "block", "type": "pdf", "pdf": {"type": "external", "external": {"url": url}}}


def pdf_block_upload(upload_id: str) -> JsonObject:
    """Build a Notion pdf block from a file upload ID."""
    return {"object": "block", "type": "pdf", "pdf": {"type": "file_upload", "file_upload": {"id": upload_id}}}


def file_block(url: str, filename: str = "") -> JsonObject:
    """Build a Notion file block for a generic attachment."""
    payload: JsonObject = {"object": "block", "type": "file", "file": {"type": "external", "external": {"url": url}}}
    if filename:
        payload["file"]["name"] = filename
    return payload


def file_block_upload(upload_id: str, filename: str = "") -> JsonObject:
    """Build a Notion file block from a file upload ID."""
    payload: JsonObject = {"object": "block", "type": "file", "file": {"type": "file_upload", "file_upload": {"id": upload_id}}}
    if filename:
        payload["file"]["name"] = filename
    return payload


def heading_block(text: str, level: int = 1) -> JsonObject:
    """Build a Notion heading block (level 1-3)."""
    level = max(1, min(3, level))
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": [plain_text_span(text)]}}


def bulleted_list_item_block(text: str) -> JsonObject:
    """Build a Notion bulleted list item block."""
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [plain_text_span(text)]}}


def numbered_list_item_block(text: str) -> JsonObject:
    """Build a Notion numbered list item block."""
    return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [plain_text_span(text)]}}


def quote_block(text: str) -> JsonObject:
    """Build a Notion quote block."""
    return {"object": "block", "type": "quote", "quote": {"rich_text": [plain_text_span(text)]}}


def code_block(text: str, language: str = "plain text") -> JsonObject:
    """Build a Notion code block."""
    return {"object": "block", "type": "code", "code": {"rich_text": [plain_text_span(text)], "language": language}}


def divider_block() -> JsonObject:
    """Build a Notion divider block."""
    return {"object": "block", "type": "divider", "divider": {}}


def todo_block(text: str, checked: bool = False) -> JsonObject:
    """Build a Notion to_do block."""
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": [plain_text_span(text)], "checked": checked}}


def table_block(rows: list[list[str]]) -> JsonObject:
    """Build a Notion table block with table_row children."""
    width = max(len(row) for row in rows) if rows else 0
    children = []
    for row in rows:
        cells = [[plain_text_span(cell)] for cell in row]
        # Pad short rows
        while len(cells) < width:
            cells.append([plain_text_span("")])
        children.append({"object": "block", "type": "table_row", "table_row": {"cells": cells}})
    return {
        "object": "block",
        "type": "table",
        "table": {"table_width": width, "has_column_header": True, "has_row_header": False, "children": children},
    }


def annotated_text_span(text: str, annotations: dict) -> JsonObject:
    """Build a Notion rich_text span with formatting annotations."""
    span = plain_text_span(text)
    span["annotations"] = {
        "bold": annotations.get("bold", False),
        "italic": annotations.get("italic", False),
        "strikethrough": annotations.get("strikethrough", False),
        "underline": annotations.get("underline", False),
        "code": annotations.get("code", False),
        "color": "default",
    }
    return span


# ---------------------------------------------------------------------------
# Block decomposition (REQ-BLOCK-02, REQ-BLOCK-03, REQ-LINK-01, REQ-LINK-02,
#                      REQ-UNSUPPORTED-01)
# ---------------------------------------------------------------------------

def segments_to_notion_blocks(
    segments: Sequence[Any],  # Sequence[ContentSegment] — imported lazily to avoid circularity
    resource_map: dict[str, str],
    note_id: str = "",
    note_title: str = "",
) -> tuple[list[JsonObject], list[UnsupportedContentRecord | EvernoteEmbeddedLinkRecord]]:
    """Convert planned content segments into Notion block JSON payloads.

    Consecutive ``text`` and ``http_link`` segments are merged into a single
    paragraph block with appropriate rich_text annotations.  Non-inline segments
    (resources, evernote links, tables) flush any pending inline run first, then
    emit their own block.

    Args:
        segments:     Ordered sequence of ``ContentSegment`` objects from ``plan_enml_segments``.
        resource_map: Mapping of resource hash → uploaded Notion file URL.  Resources
                      absent from the map emit an unsupported-content placeholder.
        note_id:      Source note identifier — recorded on any emitted exception records.
        note_title:   Source note title — recorded on any emitted exception records.

    Returns:
        A 2-tuple of (notion_blocks, exception_records).
    """
    blocks: list[JsonObject] = []
    exception_records: list[UnsupportedContentRecord | EvernoteEmbeddedLinkRecord] = []
    pending_inline: list[JsonObject] = []

    def flush_inline() -> None:
        if pending_inline:
            blocks.append(paragraph_block(list(pending_inline)))
            pending_inline.clear()

    def append_unsupported(segment: Any, reason: str) -> None:
        record = UnsupportedContentRecord(
            note_id=note_id,
            note_title=note_title,
            error_comment=f"{segment.text} — {reason}",
        )
        exception_records.append(record)
        blocks.append(unsupported_content_marker_block(record))

    for segment in segments:
        kind = segment.kind

        if kind == "text":
            if not segment.inline:
                # Block-level text (from div/p boundary) — new paragraph
                flush_inline()
            if segment.text:
                if segment.annotations:
                    pending_inline.append(annotated_text_span(segment.text, segment.annotations))
                else:
                    pending_inline.append(plain_text_span(segment.text))

        elif kind == "http_link":
            # Inline link annotation — stays within the current paragraph run.
            pending_inline.append(link_text_span(segment.text, segment.value))

        elif kind == "heading":
            flush_inline()
            blocks.append(heading_block(segment.text, segment.level))

        elif kind == "bulleted_list":
            flush_inline()
            blocks.append(bulleted_list_item_block(segment.text))

        elif kind == "numbered_list":
            flush_inline()
            blocks.append(numbered_list_item_block(segment.text))

        elif kind == "quote":
            flush_inline()
            blocks.append(quote_block(segment.text))

        elif kind == "code":
            flush_inline()
            blocks.append(code_block(segment.text))

        elif kind == "divider":
            flush_inline()
            blocks.append(divider_block())

        elif kind == "to_do":
            flush_inline()
            blocks.append(todo_block(segment.text, segment.checked))

        elif kind == "encrypted":
            flush_inline()
            hint_msg = f" (hint: {segment.value})" if segment.value else ""
            record = UnsupportedContentRecord(
                note_id=note_id,
                note_title=note_title,
                error_comment=f"Encrypted content requires passphrase{hint_msg} — cannot be imported automatically",
            )
            exception_records.append(record)
            blocks.append(unsupported_content_marker_block(record))

        elif kind == "evernote_link":
            # Cannot be resolved until all notes are imported (REQ-LINK-02).
            flush_inline()
            record = EvernoteEmbeddedLinkRecord(
                note_id=note_id,
                note_title=note_title,
                link_text=segment.text,
                link_value=segment.value,
            )
            exception_records.append(record)
            blocks.append(evernote_embedded_link_marker_block(record))

        elif kind == "resource":
            flush_inline()
            resource_ref = resource_map.get(segment.value, "")
            block_type = mime_to_notion_block_type(segment.mime_type)
            if block_type == "unsupported":
                append_unsupported(segment, f"MIME type {segment.mime_type!r} is not supported by the Notion API")
            elif not resource_ref:
                # Resource not in manifest at all — truly missing
                append_unsupported(segment, f"{segment.mime_type or 'unknown'} resource not found in resource map")
            elif resource_ref.startswith("upload:"):
                # File was uploaded — use file_upload block type
                upload_id = resource_ref[7:]  # Strip "upload:" prefix
                if block_type == "image":
                    blocks.append(image_block_upload(upload_id))
                elif block_type == "pdf":
                    blocks.append(pdf_block_upload(upload_id))
                else:
                    blocks.append(file_block_upload(upload_id))
            elif resource_ref.startswith("http://") or resource_ref.startswith("https://"):
                # External URL
                if block_type == "image":
                    blocks.append(image_block(resource_ref))
                elif block_type == "pdf":
                    blocks.append(pdf_block(resource_ref))
                else:
                    blocks.append(file_block(resource_ref))
            else:
                # Local file path — upload failed or not attempted
                append_unsupported(
                    segment,
                    f"{segment.mime_type or 'file'} attachment available locally: {resource_ref}. "
                    f"Use resolution workbench to upload.",
                )

        elif kind == "table":
            flush_inline()
            if hasattr(segment, "rows") and segment.rows:
                blocks.append(table_block(segment.rows))
            else:
                append_unsupported(segment, "HTML table — no row data available; manual insertion required")

    flush_inline()
    return blocks, exception_records


class NotionAPIError(RuntimeError):
    """Raised when Notion rejects an API request."""


@dataclass(frozen=True)
class NotionPageRef:
    """Minimal page metadata needed by the migration bootstrap."""

    page_id: str
    title: str
    url: str | None
    parent_page_id: str | None
    parent_database_id: str | None = None
    parent_type: str = ""


@dataclass(frozen=True)
class NotionBootstrapResult:
    """Pages created or reused for one migration workspace."""

    root: NotionPageRef
    converted: NotionPageRef
    exceptions: NotionPageRef


@dataclass(frozen=True)
class NotionDatabaseRef:
    """Minimal database metadata needed for idempotent import setup."""

    database_id: str
    title: str
    url: str | None
    parent_page_id: str | None


class NotionClient:
    """Migration-focused wrapper around the Notion Python SDK."""

    def __init__(self, notion_key: str, sdk_client: Any | None = None) -> None:
        if sdk_client is None:
            try:
                from notion_client import Client
            except ImportError as exc:
                raise NotionAPIError("Install notion-client to use Notion API features") from exc
            sdk_client = Client(auth=notion_key, notion_version="2022-06-28")
        self._sdk_client = sdk_client

    def search_pages(self, query: str | None = None) -> list[NotionPageRef]:
        """Return pages shared with the integration, optionally filtered by title."""
        pages: list[NotionPageRef] = []
        start_cursor: str | None = None

        while True:
            body: JsonObject = {
                "filter": {"property": "object", "value": "page"},
                "page_size": 100,
            }
            if query:
                body["query"] = query
            if start_cursor:
                body["start_cursor"] = start_cursor

            response = self._sdk_call(self._sdk_client.search, **body)
            pages.extend(_page_ref(page) for page in response.get("results", []))
            if not response.get("has_more"):
                return pages
            start_cursor = response.get("next_cursor")

    def search_databases(self, query: str | None = None) -> list[NotionDatabaseRef]:
        """Return databases shared with the integration, optionally filtered by title."""
        databases: list[NotionDatabaseRef] = []
        start_cursor: str | None = None

        while True:
            body: JsonObject = {
                "filter": {"property": "object", "value": "database"},
                "page_size": 100,
            }
            if query:
                body["query"] = query
            if start_cursor:
                body["start_cursor"] = start_cursor

            response = self._sdk_call(self._sdk_client.search, **body)
            databases.extend(_database_ref(database) for database in response.get("results", []))
            if not response.get("has_more"):
                return databases
            start_cursor = response.get("next_cursor")

    def create_page(self, parent_page_id: str, title: str) -> NotionPageRef:
        """Create a new child page under an existing Notion page."""
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": [{"text": {"content": title}}]},
        }
        return _page_ref(self._sdk_call(self._sdk_client.pages.create, **body))

    def create_workspace_page(self, title: str) -> NotionPageRef:
        """Create a top-level workspace page when the integration type allows it."""
        body = {
            "parent": {"type": "workspace", "workspace": True},
            "properties": {"title": [{"text": {"content": title}}]},
        }
        return _page_ref(self._sdk_call(self._sdk_client.pages.create, **body))

    def create_database(self, parent_page_id: str, title: str, properties: JsonObject) -> NotionDatabaseRef:
        """Create a child database under a Notion page with schema properties."""
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        return _database_ref(self._api("databases", "POST", body))

    def create_database_row(self, database_id: str, title: str, tags: tuple[str, ...] | list[str]) -> NotionPageRef:
        """Create one page row in an import database."""
        body = {
            "parent": {"database_id": database_id},
            "properties": {
                "Name": {"title": [{"text": {"content": title}}]},
                IMPORT_TAGS_PROPERTY: import_tags_property(tags),
            },
        }
        return _page_ref(self._sdk_call(self._sdk_client.pages.create, **body))

    def create_database_page(self, database_id: str, properties: JsonObject) -> NotionPageRef:
        """Create one database row page with custom properties."""
        body = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        return _page_ref(self._sdk_call(self._sdk_client.pages.create, **body))

    def update_page_properties(self, page_id: str, properties: JsonObject) -> NotionPageRef:
        """Update page/database-row properties."""
        return _page_ref(self._sdk_call(self._sdk_client.pages.update, page_id=page_id, properties=properties))

    def retrieve_page_raw(self, page_id: str) -> JsonObject:
        """Retrieve raw page payload including properties."""
        return self._sdk_call(self._sdk_client.pages.retrieve, page_id=page_id)

    def list_block_children(self, block_id: str) -> list[JsonObject]:
        """List first-level child blocks for one block/page id."""
        children: list[JsonObject] = []
        start_cursor: str | None = None
        while True:
            body: JsonObject = {"block_id": block_id, "page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor
            response = self._sdk_call(self._sdk_client.blocks.children.list, **body)
            children.extend(response.get("results", []))
            if not response.get("has_more"):
                return children
            start_cursor = response.get("next_cursor")

    def archive_page(self, page_id: str) -> NotionPageRef:
        """Archive one Notion page by id for cleanup workflows."""
        return _page_ref(self._sdk_call(self._sdk_client.pages.update, page_id=page_id, archived=True))

    def update_block_with_page_link(self, block_id: str, link_text: str, page_url: str) -> JsonObject:
        """Replace a warning placeholder block with a paragraph containing an inline page link."""
        body = {
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": link_text,
                            "link": {"url": page_url},
                        },
                    }
                ]
            }
        }
        return self._sdk_call(self._sdk_client.blocks.update, block_id=block_id, **body)

    def upload_file(self, file_path: "Path", mime_type: str = "") -> str:
        """Upload a local file via Notion File Upload API and return the upload ID.

        Two-step process: create upload object, then send file contents.
        """
        from pathlib import Path as _Path
        import mimetypes
        local_path = _Path(file_path)
        if not local_path.exists():
            raise NotionAPIError(f"File not found: {file_path}")

        # Determine content type — prefer explicit mime_type, fall back to extension guess
        content_type = mime_type or mimetypes.guess_type(str(local_path))[0] or ""
        if not content_type or content_type == "application/octet-stream":
            # Notion rejects octet-stream — try harder based on extension
            ext_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
                ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".wav": "audio/wav",
                ".mp4": "video/mp4", ".mov": "video/quicktime",
            }
            content_type = ext_map.get(local_path.suffix.lower(), "")
            if not content_type:
                raise NotionAPIError(f"Cannot determine content type for: {local_path.name}")

        # Step 1: Create upload object
        create_response = self._api("file_uploads", "POST", {})
        upload_id = create_response["id"]

        # Step 2: Send file contents via form_data
        file_data = local_path.read_bytes()
        self._sdk_call(
            self._sdk_client.request,
            path=f"file_uploads/{upload_id}/send",
            method="POST",
            form_data={"file": (local_path.name, file_data, content_type)},
        )
        return upload_id

    def append_blocks_batched(self, page_id: str, blocks: list[JsonObject]) -> None:
        """Append blocks to a page in Notion-safe batches of ≤100."""
        for i in range(0, len(blocks), 100):
            batch = blocks[i : i + 100]
            self._sdk_call(
                self._sdk_client.blocks.children.append,
                block_id=page_id,
                children=batch,
            )

    def import_note_blocks(
        self,
        database_id: str,
        title: str,
        tags: tuple[str, ...] | list[str],
        blocks: list[JsonObject],
    ) -> str:
        """Create a database row with content blocks, using minimal API calls.

        First 100 blocks are included in pages.create. Overflow is appended in batches.
        Returns the created page_id.
        """
        initial = blocks[:100]
        overflow = blocks[100:]

        properties: JsonObject = {
            "Name": {"title": [{"text": {"content": title}}]},
        }
        if tags:
            properties[IMPORT_TAGS_PROPERTY] = import_tags_property(tags)

        body: JsonObject = {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": initial,
        }

        try:
            page = self._sdk_call(self._sdk_client.pages.create, **body)
        except NotionAPIError as exc:
            if "not a property that exists" in str(exc) and IMPORT_TAGS_PROPERTY in str(exc):
                # Tags property doesn't exist on this database — retry without it
                properties.pop(IMPORT_TAGS_PROPERTY, None)
                body["properties"] = properties
                page = self._sdk_call(self._sdk_client.pages.create, **body)
            else:
                raise

        page_id = page["id"]

        if overflow:
            self.append_blocks_batched(page_id, overflow)

        return page_id

    def delete_block(self, block_id: str) -> None:
        """Delete a block by ID. Handles already-deleted blocks gracefully."""
        try:
            self._sdk_call(self._sdk_client.blocks.delete, block_id=block_id)
        except NotionAPIError:
            pass  # Block already deleted or not found — acceptable

    def _api(self, path: str, method: str = "GET", body: JsonObject | None = None) -> JsonObject:
        """Direct Notion API call bypassing SDK convenience method filters."""
        kwargs: dict[str, Any] = {"path": path, "method": method}
        if body is not None:
            kwargs["body"] = body
        return self._sdk_call(self._sdk_client.request, **kwargs)

    def _sdk_call(self, sdk_method: Any, **kwargs: Any) -> JsonObject:
        try:
            return sdk_method(**kwargs)
        except Exception as exc:
            raise NotionAPIError(f"Notion SDK request failed: {exc}") from exc


def multi_select_property(values: tuple[str, ...] | list[str]) -> JsonObject:
    """Build a Notion multi-select property value from unique non-empty values."""
    unique_values = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    return {"multi_select": [{"name": value} for value in unique_values]}


def exception_reason_property(reasons: tuple[str, ...] | list[str]) -> JsonObject:
    """Build the exception database Reason multi-select property."""
    return multi_select_property(reasons)


def import_tags_property(tags: tuple[str, ...] | list[str]) -> JsonObject:
    """Build the imported note database Tags multi-select property."""
    return multi_select_property(tags)


def import_database_properties() -> JsonObject:
    """Return the schema for one imported ENEX database."""
    return {
        "Name": {"title": {}},
        IMPORT_TAGS_PROPERTY: {"multi_select": {}},
    }


def exception_database_properties() -> JsonObject:
    """Return the schema for the Import-Exceptions database."""
    return {
        "Note Name": {"title": {}},
        EXCEPTION_KEY_PROPERTY: {"rich_text": {}},
        EXCEPTION_STATUS_PROPERTY: {"select": {"options": [{"name": "Open"}, {"name": "Resolved"}, {"name": "Closed"}]}},
        "Link": {"url": {}},
        EXCEPTION_REASON_PROPERTY: {"multi_select": {}},
        "Error Message": {"rich_text": {}},
        "Source File": {"rich_text": {}},
        "Linkable Text": {"rich_text": {}},
        "Evernote Attribute": {"rich_text": {}},
        "Notion Target": {"rich_text": {}},
        "External Resource": {"rich_text": {}},
    }


def unsupported_content_marker_block(record: UnsupportedContentRecord) -> JsonObject:
    """Build a visible Notion block for content that could not be imported."""
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": record.marker_text}}],
            "icon": {"type": "emoji", "emoji": "❗"},
            "color": "yellow_background",
        },
    }


def evernote_embedded_link_marker_block(record: EvernoteEmbeddedLinkRecord) -> JsonObject:
    """Build a visible warning block for an unresolved Evernote embedded link."""
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": record.marker_text}}],
            "icon": {"type": "emoji", "emoji": "⚠️"},
            "color": "yellow_background",
        },
    }


def bootstrap_notion_pages(
    notion_key: str,
    root_title: str | None = None,
    converted_title: str = DEFAULT_CONVERTED_PAGE_TITLE,
    exceptions_title: str = DEFAULT_EXCEPTIONS_PAGE_TITLE,
    *,
    client: NotionClient | None = None,
) -> NotionBootstrapResult:
    """Create or reuse the Notion pages required for migration output.

    When ``root_title`` is provided, the function uses the matching shared page as
    the parent. Without it, the function creates or reuses top-level workspace
    pages for the migration.
    """
    if not notion_key.strip():
        raise ValueError("notion_key is required")

    notion = client or NotionClient(notion_key)
    if root_title is None:
        all_visible_pages = notion.search_pages()
        workspace = NotionPageRef(
            page_id="",
            title="Workspace",
            url=None,
            parent_page_id=None,
            parent_type="workspace",
        )
        converted = _find_workspace_page(all_visible_pages, converted_title)
        if converted is None:
            converted = notion.create_workspace_page(converted_title)

        exceptions = _find_workspace_page(all_visible_pages, exceptions_title)
        if exceptions is None:
            exceptions = notion.create_workspace_page(exceptions_title)

        return NotionBootstrapResult(root=workspace, converted=converted, exceptions=exceptions)

    shared_pages = notion.search_pages(root_title)
    root = _select_root_page(shared_pages, root_title)
    if root is None:
        raise ValueError(
            f"Could not find a Notion page titled {root_title!r}. "
            f"Please create a page with that title in Notion and share it with your integration "
            f"(page → ··· → Connections → Add your integration)."
        )
    all_visible_pages = notion.search_pages()

    converted = _find_child_page(all_visible_pages, root.page_id, converted_title)
    if converted is None:
        converted = notion.create_page(root.page_id, converted_title)

    exceptions = _find_child_page(all_visible_pages, root.page_id, exceptions_title)
    if exceptions is None:
        exceptions = notion.create_page(root.page_id, exceptions_title)

    return NotionBootstrapResult(root=root, converted=converted, exceptions=exceptions)


def ensure_import_database(client: NotionClient, parent_page_id: str, database_title: str) -> NotionDatabaseRef:
    """Create or reuse the import database for one ENEX file under Evernote Import."""
    return ensure_child_database(client, parent_page_id, database_title, import_database_properties())


def ensure_exception_database(client: NotionClient, parent_page_id: str) -> NotionDatabaseRef:
    """Create or reuse the single Import-Exceptions database."""
    return ensure_child_database(
        client,
        parent_page_id,
        DEFAULT_EXCEPTIONS_DATABASE_TITLE,
        exception_database_properties(),
    )


def ensure_child_database(
    client: NotionClient,
    parent_page_id: str,
    database_title: str,
    properties: JsonObject,
) -> NotionDatabaseRef:
    """Create a child database only when an exact existing sibling is absent.

    If the database already exists, ensures all expected properties are present
    (adds missing properties without modifying existing ones).
    """
    existing = _find_child_database(client.search_databases(database_title), parent_page_id, database_title)
    if existing is not None:
        # Ensure schema has all expected properties
        try:
            props_to_add = {k: v for k, v in properties.items() if k != "Name" and k != "Note Name"}
            if props_to_add:
                client._sdk_call(
                    client._sdk_client.databases.update,
                    database_id=existing.database_id,
                    properties=props_to_add,
                )
        except Exception as exc:
            import logging
            logging.getLogger("e2n.notion").warning("Could not update database schema: %s", exc)
        return existing
    return client.create_database(parent_page_id, database_title, properties)


def create_exception_row(
    client: NotionClient,
    exception_database_id: str,
    note_name: str,
    reasons: tuple[str, ...] | list[str],
    error_message: str = "",
    source_file: str = "",
    link_text: str = "",
    link_value: str = "",
    page_url: str = "",
) -> str:
    """Create one row in the Import-Exceptions database. Returns the row page_id."""
    properties: JsonObject = {
        "Note Name": {"title": [{"text": {"content": note_name}}]},
        EXCEPTION_REASON_PROPERTY: exception_reason_property(reasons),
    }
    if error_message:
        properties["Error Message"] = {"rich_text": [{"text": {"content": error_message[:2000]}}]}
    if source_file:
        properties["Source File"] = {"rich_text": [{"text": {"content": source_file[:2000]}}]}
    if link_text:
        properties["Linkable Text"] = {"rich_text": [{"text": {"content": link_text[:2000]}}]}
    if link_value:
        properties["Linkable Text"] = {"rich_text": [{"text": {"content": link_value[:2000]}}]}
    if page_url:
        properties["Link"] = {"url": page_url}

    body: JsonObject = {
        "parent": {"database_id": exception_database_id},
        "properties": properties,
    }

    try:
        response = client._sdk_call(client._sdk_client.pages.create, **body)
        return response["id"]
    except NotionAPIError as exc:
        if "not a property that exists" in str(exc):
            # Fallback: create with just the title (schema may not be set up)
            fallback_body: JsonObject = {
                "parent": {"database_id": exception_database_id},
                "properties": {"title": {"title": [{"text": {"content": note_name}}]}},
            }
            try:
                response = client._sdk_call(client._sdk_client.pages.create, **fallback_body)
                return response["id"]
            except Exception:
                pass
        raise


def _select_root_page(pages: list[NotionPageRef], root_title: str | None) -> NotionPageRef | None:
    """Find the shared page matching root_title, or return None if not found."""
    if root_title is not None:
        matches = [page for page in pages if page.title == root_title]
        if not matches:
            return None
        return _deepest_page(matches)
    return None


def _deepest_page(pages: list[NotionPageRef]) -> NotionPageRef:
    by_id = {page.page_id: page for page in pages}

    def depth(page: NotionPageRef) -> int:
        seen: set[str] = set()
        current = page
        distance = 0
        while current.parent_page_id and current.parent_page_id in by_id and current.parent_page_id not in seen:
            seen.add(current.page_id)
            current = by_id[current.parent_page_id]
            distance += 1
        return distance

    return max(pages, key=lambda page: (depth(page), page.title.lower(), page.page_id))


def _find_child_page(pages: list[NotionPageRef], parent_page_id: str, title: str) -> NotionPageRef | None:
    matches = [page for page in pages if page.parent_page_id == parent_page_id and page.title == title]
    return matches[0] if matches else None


def _find_workspace_page(pages: list[NotionPageRef], title: str) -> NotionPageRef | None:
    matches = [page for page in pages if page.parent_type == "workspace" and page.title == title]
    return matches[0] if matches else None


def _find_child_database(
    databases: list[NotionDatabaseRef],
    parent_page_id: str,
    title: str,
) -> NotionDatabaseRef | None:
    matches = [database for database in databases if database.parent_page_id == parent_page_id and database.title == title]
    return matches[0] if matches else None


def _page_ref(page: JsonObject) -> NotionPageRef:
    parent = page.get("parent", {})
    parent_page_id = parent.get("page_id") if (parent.get("type") == "page_id" or "page_id" in parent) else None
    parent_database_id = (
        parent.get("database_id") if (parent.get("type") == "database_id" or "database_id" in parent) else None
    )
    return NotionPageRef(
        page_id=page["id"],
        title=_page_title(page),
        url=page.get("url"),
        parent_page_id=parent_page_id,
        parent_database_id=parent_database_id,
        parent_type=parent.get("type", ""),
    )


def _database_ref(database: JsonObject) -> NotionDatabaseRef:
    parent = database.get("parent", {})
    parent_page_id = parent.get("page_id") if parent.get("type") == "page_id" else None
    return NotionDatabaseRef(
        database_id=database["id"],
        title=_title_text(database.get("title", [])),
        url=database.get("url"),
        parent_page_id=parent_page_id,
    )


def _page_title(page: JsonObject) -> str:
    properties = page.get("properties", {})
    for value in properties.values():
        if value.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in value.get("title", [])).strip()
    return ""


def _title_text(title_items: list[JsonObject]) -> str:
    return "".join(part.get("plain_text", "") for part in title_items).strip()
