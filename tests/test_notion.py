from e2n.cli import main
from e2n.exceptions import EvernoteEmbeddedLinkRecord, UnsupportedContentRecord
from e2n.notion import (
    DEFAULT_EXCEPTIONS_DATABASE_TITLE,
    NotionClient,
    NotionDatabaseRef,
    NotionPageRef,
    bootstrap_notion_pages,
    evernote_embedded_link_marker_block,
    ensure_exception_database,
    ensure_import_database,
    exception_reason_property,
    file_block,
    image_block,
    import_tags_property,
    link_text_span,
    mime_to_notion_block_type,
    paragraph_block,
    pdf_block,
    plain_text_span,
    segments_to_notion_blocks,
    unsupported_content_marker_block,
)


class FakeNotionClient:
    def __init__(self, pages: list[NotionPageRef], databases: list[NotionDatabaseRef] | None = None) -> None:
        self.pages = pages
        self.databases = databases or []
        self.created: list[tuple[str, str]] = []
        self.created_databases: list[tuple[str, str]] = []

    def search_pages(self, query: str | None = None) -> list[NotionPageRef]:
        if query is None:
            return list(self.pages)
        return [page for page in self.pages if query in page.title]

    def search_databases(self, query: str | None = None) -> list[NotionDatabaseRef]:
        if query is None:
            return list(self.databases)
        return [database for database in self.databases if query in database.title]

    def create_page(self, parent_page_id: str, title: str) -> NotionPageRef:
        page = NotionPageRef(
            page_id=f"created-{len(self.created) + 1}",
            title=title,
            url=None,
            parent_page_id=parent_page_id,
        )
        self.created.append((parent_page_id, title))
        self.pages.append(page)
        return page

    def create_workspace_page(self, title: str) -> NotionPageRef:
        page = NotionPageRef(
            page_id=f"workspace-created-{len(self.created) + 1}",
            title=title,
            url=None,
            parent_page_id=None,
            parent_type="workspace",
        )
        self.created.append(("workspace", title))
        self.pages.append(page)
        return page

    def create_database(self, parent_page_id: str, title: str, properties: dict) -> NotionDatabaseRef:
        database = NotionDatabaseRef(
            database_id=f"database-created-{len(self.created_databases) + 1}",
            title=title,
            url=None,
            parent_page_id=parent_page_id,
        )
        self.created_databases.append((parent_page_id, title))
        self.databases.append(database)
        return database


class FakePagesEndpoint:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        if "title" in kwargs["properties"]:
            title_text = kwargs["properties"]["title"][0]["text"]["content"]
        else:
            title_text = kwargs["properties"]["Name"]["title"][0]["text"]["content"]
        return {
            "id": "created",
            "url": "https://notion.so/created",
            "parent": kwargs["parent"],
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"plain_text": title_text}],
                }
            },
        }

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {
            "id": kwargs["page_id"],
            "url": f"https://notion.so/{kwargs['page_id']}",
            "parent": {"type": "page_id", "page_id": "root"},
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"plain_text": "Archived"}],
                }
            },
        }


class FakeSDKClient:
    def __init__(self) -> None:
        self.searches: list[dict] = []
        self.pages = FakePagesEndpoint()

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return {
            "results": [
                {
                    "id": "root",
                    "url": "https://notion.so/root",
                    "parent": {"type": "workspace", "workspace": True},
                    "properties": {
                        "title": {
                            "type": "title",
                            "title": [{"plain_text": "Root"}],
                        }
                    },
                }
            ],
            "has_more": False,
        }


def test_notion_client_uses_sdk_for_search_and_page_creation() -> None:
    sdk_client = FakeSDKClient()
    notion = NotionClient("notion-key", sdk_client=sdk_client)

    pages = notion.search_pages("Root")
    created = notion.create_page("root", "Evernote Import")

    assert pages == [
        NotionPageRef(
            page_id="root",
            title="Root",
            url="https://notion.so/root",
            parent_page_id=None,
            parent_type="workspace",
        )
    ]
    assert sdk_client.searches == [
        {
            "filter": {"property": "object", "value": "page"},
            "page_size": 100,
            "query": "Root",
        }
    ]
    assert created == NotionPageRef(
        page_id="created",
        title="Evernote Import",
        url="https://notion.so/created",
        parent_page_id="root",
        parent_type="page_id",
    )
    assert sdk_client.pages.created == [
        {
            "parent": {"type": "page_id", "page_id": "root"},
            "properties": {"title": [{"text": {"content": "Evernote Import"}}]},
        }
    ]


def test_notion_client_can_create_workspace_page() -> None:
    sdk_client = FakeSDKClient()
    notion = NotionClient("notion-key", sdk_client=sdk_client)

    created = notion.create_workspace_page("Evernote Import")

    assert created == NotionPageRef(
        page_id="created",
        title="Evernote Import",
        url="https://notion.so/created",
        parent_page_id=None,
        parent_type="workspace",
    )
    assert sdk_client.pages.created == [
        {
            "parent": {"type": "workspace", "workspace": True},
            "properties": {"title": [{"text": {"content": "Evernote Import"}}]},
        }
    ]


def test_notion_client_can_create_database_row() -> None:
    sdk_client = FakeSDKClient()
    notion = NotionClient("notion-key", sdk_client=sdk_client)

    page = notion.create_database_row("database-1", "Imported Note", ["Project", "Archive"])

    assert page.page_id == "created"
    assert sdk_client.pages.created == [
        {
            "parent": {"database_id": "database-1"},
            "properties": {
                "Name": {"title": [{"text": {"content": "Imported Note"}}]},
                "Tags": {"multi_select": [{"name": "Project"}, {"name": "Archive"}]},
            },
        }
    ]


def test_notion_client_can_archive_page() -> None:
    sdk_client = FakeSDKClient()
    notion = NotionClient("notion-key", sdk_client=sdk_client)

    archived = notion.archive_page("page-1")

    assert archived.page_id == "page-1"
    assert archived.title == "Archived"
    assert sdk_client.pages.updated == [{"page_id": "page-1", "archived": True}]


def test_multi_select_properties_discard_blanks_and_preserve_order() -> None:
    assert exception_reason_property(["Empty Title", "No Content", "Empty Title", " "]) == {
        "multi_select": [{"name": "Empty Title"}, {"name": "No Content"}]
    }
    assert import_tags_property(["Project", "Archive"]) == {
        "multi_select": [{"name": "Project"}, {"name": "Archive"}]
    }


def test_unsupported_content_marker_block_uses_error_comment() -> None:
    record = UnsupportedContentRecord(
        note_id="note_000003",
        note_title="Unsupported Note",
        error_comment="Unsupported media payload.",
    )

    assert unsupported_content_marker_block(record) == {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": "Unsupported content could not be imported automatically: Unsupported media payload."
                    },
                }
            ],
            "icon": {"type": "emoji", "emoji": "❗"},
            "color": "yellow_background",
        },
    }


def test_evernote_embedded_link_marker_block_uses_warning_icon_and_link_text() -> None:
    record = EvernoteEmbeddedLinkRecord(
        note_id="note_000004",
        note_title="Linked Note",
        link_text="Original Evernote Note",
        link_value="evernote:///view/123/s1/guid/guid/",
    )

    assert evernote_embedded_link_marker_block(record) == {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": "Evernote link requires manual resolution: Original Evernote Note"
                    },
                }
            ],
            "icon": {"type": "emoji", "emoji": "⚠️"},
            "color": "yellow_background",
        },
    }


def test_bootstrap_notion_pages_creates_children_under_named_root() -> None:
    root = NotionPageRef(page_id="root", title="Migration Root", url=None, parent_page_id=None)
    client = FakeNotionClient([root])

    result = bootstrap_notion_pages("notion-key", root_title="Migration Root", client=client)

    assert result.root == root
    assert result.converted.title == "Evernote Import"
    assert result.exceptions.title == "Evernote Import Exceptions"
    assert client.created == [("root", "Evernote Import"), ("root", "Evernote Import Exceptions")]


def test_bootstrap_notion_pages_reuses_existing_child_pages() -> None:
    root = NotionPageRef(page_id="root", title="Migration Root", url=None, parent_page_id=None)
    converted = NotionPageRef(page_id="converted", title="Evernote Import", url=None, parent_page_id="root")
    exceptions = NotionPageRef(page_id="exceptions", title="Evernote Import Exceptions", url=None, parent_page_id="root")
    client = FakeNotionClient([root, converted, exceptions])

    result = bootstrap_notion_pages("notion-key", root_title="Migration Root", client=client)

    assert result.converted == converted
    assert result.exceptions == exceptions
    assert client.created == []


def test_bootstrap_notion_pages_uses_workspace_pages_without_root_title() -> None:
    root = NotionPageRef(page_id="root", title="Root", url=None, parent_page_id=None)
    child = NotionPageRef(page_id="child", title="Child", url=None, parent_page_id="root")
    grandchild = NotionPageRef(page_id="grandchild", title="Grandchild", url=None, parent_page_id="child")
    client = FakeNotionClient([root, child, grandchild])

    result = bootstrap_notion_pages("notion-key", client=client)

    assert result.root.title == "Workspace"
    assert result.root.parent_type == "workspace"
    assert client.created == [("workspace", "Evernote Import"), ("workspace", "Evernote Import Exceptions")]


def test_bootstrap_notion_pages_reuses_existing_workspace_pages_without_root_title() -> None:
    converted = NotionPageRef(
        page_id="converted",
        title="Evernote Import",
        url=None,
        parent_page_id=None,
        parent_type="workspace",
    )
    exceptions = NotionPageRef(
        page_id="exceptions",
        title="Evernote Import Exceptions",
        url=None,
        parent_page_id=None,
        parent_type="workspace",
    )
    nested = NotionPageRef(page_id="nested", title="Evernote Import", url=None, parent_page_id="parent")
    client = FakeNotionClient([converted, exceptions, nested])

    result = bootstrap_notion_pages("notion-key", client=client)

    assert result.converted == converted
    assert result.exceptions == exceptions
    assert client.created == []


def test_ensure_import_database_reuses_same_name_under_same_parent() -> None:
    existing = NotionDatabaseRef(
        database_id="existing",
        title="Enduring",
        url=None,
        parent_page_id="converted",
    )
    same_name_elsewhere = NotionDatabaseRef(
        database_id="elsewhere",
        title="Enduring",
        url=None,
        parent_page_id="other",
    )
    client = FakeNotionClient([], databases=[same_name_elsewhere, existing])

    database = ensure_import_database(client, "converted", "Enduring")

    assert database == existing
    assert client.created_databases == []


def test_ensure_import_database_creates_when_same_name_is_only_elsewhere() -> None:
    same_name_elsewhere = NotionDatabaseRef(
        database_id="elsewhere",
        title="Enduring",
        url=None,
        parent_page_id="other",
    )
    client = FakeNotionClient([], databases=[same_name_elsewhere])

    database = ensure_import_database(client, "converted", "Enduring")

    assert database.database_id == "database-created-1"
    assert client.created_databases == [("converted", "Enduring")]


def test_ensure_exception_database_uses_single_import_exceptions_name() -> None:
    client = FakeNotionClient([])

    database = ensure_exception_database(client, "exceptions-page")

    assert database.title == DEFAULT_EXCEPTIONS_DATABASE_TITLE
    assert client.created_databases == [("exceptions-page", "Import-Exceptions")]


def test_notion_bootstrap_cli_reports_pages(monkeypatch, capsys) -> None:
    root = NotionPageRef(page_id="root", title="Root", url=None, parent_page_id=None)
    converted = NotionPageRef(page_id="converted", title="Evernote Import", url=None, parent_page_id="root")
    exceptions = NotionPageRef(page_id="exceptions", title="Evernote Import Exceptions", url=None, parent_page_id="root")

    def fake_bootstrap(notion_key: str, root_title: str | None = None):
        assert notion_key == "notion-key"
        assert root_title == "Root"
        from e2n.notion import NotionBootstrapResult

        return NotionBootstrapResult(root=root, converted=converted, exceptions=exceptions)

    monkeypatch.setattr("e2n.cli.bootstrap_notion_pages", fake_bootstrap)

    exit_code = main(["--notion-bootstrap", "-k", "notion-key", "-n", "Root"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Using Notion root page: Root (root)" in captured.out
    assert "Converted page: Evernote Import (converted)" in captured.out
    assert "Exceptions page: Evernote Import Exceptions (exceptions)" in captured.out


def test_notion_bootstrap_cli_accepts_environment_key(monkeypatch, capsys) -> None:
    root = NotionPageRef(page_id="root", title="Root", url=None, parent_page_id=None)
    converted = NotionPageRef(page_id="converted", title="Evernote Import", url=None, parent_page_id="root")
    exceptions = NotionPageRef(page_id="exceptions", title="Evernote Import Exceptions", url=None, parent_page_id="root")

    def fake_bootstrap(notion_key: str, root_title: str | None = None):
        assert notion_key == "env-key"
        assert root_title == "Root"
        from e2n.notion import NotionBootstrapResult

        return NotionBootstrapResult(root=root, converted=converted, exceptions=exceptions)

    monkeypatch.setenv("NOTION_KEY", "env-key")
    monkeypatch.setattr("e2n.cli.bootstrap_notion_pages", fake_bootstrap)

    exit_code = main(["--notion-bootstrap", "-n", "Root"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Using Notion root page: Root (root)" in captured.out


# ---------------------------------------------------------------------------
# MIME type registry tests (REQ-BLOCK-03, REQ-UNSUPPORTED-01)
# ---------------------------------------------------------------------------

def test_mime_to_notion_block_type_maps_images() -> None:
    assert mime_to_notion_block_type("image/png") == "image"
    assert mime_to_notion_block_type("image/jpeg") == "image"
    assert mime_to_notion_block_type("IMAGE/GIF") == "image"


def test_mime_to_notion_block_type_maps_pdf() -> None:
    assert mime_to_notion_block_type("application/pdf") == "pdf"
    assert mime_to_notion_block_type("APPLICATION/PDF") == "pdf"


def test_mime_to_notion_block_type_maps_generic_file() -> None:
    assert mime_to_notion_block_type("application/zip") == "file"
    assert mime_to_notion_block_type("application/octet-stream") == "file"
    assert mime_to_notion_block_type("text/plain") == "file"
    assert mime_to_notion_block_type("") == "file"


def test_mime_to_notion_block_type_marks_audio_video_unsupported() -> None:
    assert mime_to_notion_block_type("audio/mpeg") == "unsupported"
    assert mime_to_notion_block_type("audio/wav") == "unsupported"
    assert mime_to_notion_block_type("video/mp4") == "unsupported"
    assert mime_to_notion_block_type("video/quicktime") == "unsupported"


def test_mime_to_notion_block_type_marks_spreadsheet_unsupported() -> None:
    assert mime_to_notion_block_type("application/vnd.ms-excel") == "unsupported"
    assert mime_to_notion_block_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") == "unsupported"


# ---------------------------------------------------------------------------
# Block builder tests (REQ-BLOCK-03, REQ-LINK-01)
# ---------------------------------------------------------------------------

def test_paragraph_block_structure() -> None:
    spans = [plain_text_span("hello")]
    assert paragraph_block(spans) == {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "hello"}}]},
    }


def test_link_text_span_carries_url() -> None:
    span = link_text_span("Click here", "https://example.com")
    assert span == {"type": "text", "text": {"content": "Click here", "link": {"url": "https://example.com"}}}


def test_image_block_structure() -> None:
    assert image_block("https://cdn.example.com/img.png") == {
        "object": "block",
        "type": "image",
        "image": {"type": "external", "external": {"url": "https://cdn.example.com/img.png"}},
    }


def test_pdf_block_structure() -> None:
    assert pdf_block("https://cdn.example.com/doc.pdf") == {
        "object": "block",
        "type": "pdf",
        "pdf": {"type": "external", "external": {"url": "https://cdn.example.com/doc.pdf"}},
    }


def test_file_block_with_filename() -> None:
    block = file_block("https://cdn.example.com/data.csv", filename="data.csv")
    assert block["type"] == "file"
    assert block["file"]["external"]["url"] == "https://cdn.example.com/data.csv"
    assert block["file"]["name"] == "data.csv"


def test_file_block_without_filename_omits_name_key() -> None:
    block = file_block("https://cdn.example.com/data.csv")
    assert "name" not in block["file"]


# ---------------------------------------------------------------------------
# segments_to_notion_blocks integration tests (REQ-BLOCK-02, REQ-BLOCK-03)
# ---------------------------------------------------------------------------

def _make_segment(kind: str, text: str, value: str = "", mime_type: str = ""):
    from e2n.enml import ContentSegment
    inline = kind in ("text", "http_link")
    return ContentSegment(kind=kind, text=text, value=value, mime_type=mime_type, inline=inline)  # type: ignore[arg-type]


def test_segments_to_notion_blocks_plain_text_becomes_paragraph() -> None:
    segments = [_make_segment("text", "Hello world")]
    blocks, exceptions = segments_to_notion_blocks(segments, {})
    assert len(blocks) == 1
    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"
    assert exceptions == []


def test_segments_to_notion_blocks_http_link_stays_inline_with_text() -> None:
    segments = [
        _make_segment("text", "Visit"),
        _make_segment("http_link", "Example", value="https://example.com"),
        _make_segment("text", "today"),
    ]
    blocks, exceptions = segments_to_notion_blocks(segments, {})
    # All three inline segments should be merged into one paragraph
    assert len(blocks) == 1
    rich_text = blocks[0]["paragraph"]["rich_text"]
    assert rich_text[0]["text"]["content"] == "Visit"
    assert rich_text[1]["text"]["link"]["url"] == "https://example.com"
    assert rich_text[2]["text"]["content"] == "today"
    assert exceptions == []


def test_segments_to_notion_blocks_resource_with_url_becomes_image_block() -> None:
    segments = [_make_segment("resource", "en-media", value="abc123", mime_type="image/png")]
    blocks, exceptions = segments_to_notion_blocks(segments, {"abc123": "https://cdn.example.com/img.png"})
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert exceptions == []


def test_segments_to_notion_blocks_pdf_resource_becomes_pdf_block() -> None:
    segments = [_make_segment("resource", "en-media", value="pdfhash", mime_type="application/pdf")]
    blocks, exceptions = segments_to_notion_blocks(segments, {"pdfhash": "https://cdn.example.com/doc.pdf"})
    assert blocks[0]["type"] == "pdf"
    assert exceptions == []


def test_segments_to_notion_blocks_missing_resource_emits_callout() -> None:
    segments = [_make_segment("resource", "en-media", value="missinghash", mime_type="image/jpeg")]
    blocks, exceptions = segments_to_notion_blocks(segments, {}, note_id="n1", note_title="Note")
    assert blocks[0]["type"] == "callout"
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], UnsupportedContentRecord)


def test_segments_to_notion_blocks_unsupported_mime_emits_callout() -> None:
    segments = [_make_segment("resource", "en-media", value="audiohash", mime_type="audio/mpeg")]
    blocks, exceptions = segments_to_notion_blocks(segments, {"audiohash": "https://example.com/audio.mp3"})
    assert blocks[0]["type"] == "callout"
    assert len(exceptions) == 1


def test_segments_to_notion_blocks_evernote_link_emits_callout_and_exception() -> None:
    segments = [_make_segment("evernote_link", "Target Note", value="evernote://view/1/s1/guid/guid/")]
    blocks, exceptions = segments_to_notion_blocks(segments, {}, note_id="n1", note_title="Source Note")
    assert blocks[0]["type"] == "callout"
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], EvernoteEmbeddedLinkRecord)
    assert exceptions[0].link_text == "Target Note"


def test_segments_to_notion_blocks_table_emits_callout_and_exception() -> None:
    segments = [_make_segment("table", "table")]
    blocks, exceptions = segments_to_notion_blocks(segments, {}, note_id="n1", note_title="Note")
    assert blocks[0]["type"] == "callout"
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], UnsupportedContentRecord)


def test_segments_to_notion_blocks_mixed_content_preserves_order() -> None:
    """Text → image → text → evernote link produces 4 blocks in order."""
    segments = [
        _make_segment("text", "Before image"),
        _make_segment("resource", "en-media", value="imghash", mime_type="image/png"),
        _make_segment("text", "After image"),
        _make_segment("evernote_link", "Linked Note", value="evernote://view/1/s1/g/g/"),
    ]
    blocks, exceptions = segments_to_notion_blocks(
        segments, {"imghash": "https://cdn.example.com/img.png"}, note_id="n1", note_title="Note"
    )
    assert [b["type"] for b in blocks] == ["paragraph", "image", "paragraph", "callout"]
    assert len(exceptions) == 1  # only the evernote link




# --- notion-import spec: block builders for new segment types ---


def test_segments_to_notion_blocks_heading_produces_heading_block() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="heading", text="My Title", level=1)]
    blocks, exceptions = segments_to_notion_blocks(segments, {})
    assert len(blocks) == 1
    assert blocks[0]["type"] == "heading_1"
    assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "My Title"


def test_segments_to_notion_blocks_heading_level_2_and_3() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [
        ContentSegment(kind="heading", text="H2", level=2),
        ContentSegment(kind="heading", text="H3", level=3),
    ]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "heading_2"
    assert blocks[1]["type"] == "heading_3"


def test_segments_to_notion_blocks_bulleted_list() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [
        ContentSegment(kind="bulleted_list", text="Item A"),
        ContentSegment(kind="bulleted_list", text="Item B"),
    ]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert len(blocks) == 2
    assert blocks[0]["type"] == "bulleted_list_item"
    assert blocks[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Item A"


def test_segments_to_notion_blocks_numbered_list() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="numbered_list", text="Step 1")]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "numbered_list_item"


def test_segments_to_notion_blocks_quote() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="quote", text="Famous words")]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "quote"
    assert blocks[0]["quote"]["rich_text"][0]["text"]["content"] == "Famous words"


def test_segments_to_notion_blocks_code() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="code", text="print('hello')")]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "code"
    assert blocks[0]["code"]["rich_text"][0]["text"]["content"] == "print('hello')"
    assert blocks[0]["code"]["language"] == "plain text"


def test_segments_to_notion_blocks_divider() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="divider", text="")]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "divider"


def test_segments_to_notion_blocks_to_do() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [
        ContentSegment(kind="to_do", text="Buy milk", checked=False),
        ContentSegment(kind="to_do", text="Done task", checked=True),
    ]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "to_do"
    assert blocks[0]["to_do"]["rich_text"][0]["text"]["content"] == "Buy milk"
    assert blocks[0]["to_do"]["checked"] is False
    assert blocks[1]["to_do"]["checked"] is True


def test_segments_to_notion_blocks_table_with_rows() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="table", text="table", rows=[["A", "B"], ["1", "2"]])]
    blocks, _ = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "table"
    assert blocks[0]["table"]["table_width"] == 2
    assert blocks[0]["table"]["has_column_header"] is True
    children = blocks[0]["table"]["children"]
    assert len(children) == 2
    assert children[0]["table_row"]["cells"][0][0]["text"]["content"] == "A"


def test_segments_to_notion_blocks_annotated_text() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="text", text="bold text", annotations={"bold": True})]
    blocks, _ = segments_to_notion_blocks(segments, {})
    rt = blocks[0]["paragraph"]["rich_text"][0]
    assert rt["text"]["content"] == "bold text"
    assert rt["annotations"]["bold"] is True


def test_segments_to_notion_blocks_encrypted_produces_callout() -> None:
    from e2n.enml import ContentSegment
    from e2n.notion import segments_to_notion_blocks

    segments = [ContentSegment(kind="encrypted", text="en-crypt", value="birthday hint")]
    blocks, exceptions = segments_to_notion_blocks(segments, {})
    assert blocks[0]["type"] == "callout"
    assert "encrypted" in blocks[0]["callout"]["rich_text"][0]["text"]["content"].lower()
    assert len(exceptions) == 1



# --- notion-import spec: File Upload, Batch Append, Full Note Import ---


def test_notion_client_upload_file_returns_upload_id(monkeypatch) -> None:
    """File upload should create upload object, send file, return usable ID."""
    from unittest.mock import MagicMock, patch
    from pathlib import Path
    from e2n.notion import NotionClient
    import tempfile

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api

    # Simulate: POST /file_uploads → {id: "upload-123", status: "pending"}
    # POST /file_uploads/upload-123/send → {id: "upload-123", status: "uploaded"}
    mock_api.request.side_effect = [
        {"id": "upload-123", "status": "pending"},
        {"id": "upload-123", "status": "uploaded"},
    ]

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG fake data")
        tmp_path = Path(f.name)

    try:
        upload_id = client.upload_file(tmp_path)
        assert upload_id == "upload-123"
        assert mock_api.request.call_count == 2
    finally:
        tmp_path.unlink()


def test_batch_append_splits_at_100_blocks() -> None:
    """Blocks exceeding 100 should be split into multiple API calls."""
    from unittest.mock import MagicMock, call
    from e2n.notion import NotionClient, plain_text_span, paragraph_block

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.blocks.children.append.return_value = {"results": []}

    blocks = [paragraph_block([plain_text_span(f"Block {i}")]) for i in range(250)]

    client.append_blocks_batched("page-abc", blocks)

    # Should be 3 calls: 100 + 100 + 50
    assert mock_api.blocks.children.append.call_count == 3
    calls = mock_api.blocks.children.append.call_args_list
    assert len(calls[0][1]["children"]) == 100
    assert len(calls[1][1]["children"]) == 100
    assert len(calls[2][1]["children"]) == 50


def test_import_note_creates_page_with_initial_blocks() -> None:
    """First 100 blocks should be included in pages.create (1 API call not 2)."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient, plain_text_span, paragraph_block

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.pages.create.return_value = {"id": "new-page-id", "url": "https://notion.so/page"}
    mock_api.blocks.children.append.return_value = {"results": []}

    blocks = [paragraph_block([plain_text_span(f"Block {i}")]) for i in range(150)]

    page_id = client.import_note_blocks(
        database_id="db-123",
        title="Test Note",
        tags=("tag1",),
        blocks=blocks,
    )

    assert page_id == "new-page-id"
    # Page creation should include first 100 blocks
    create_call = mock_api.pages.create.call_args
    assert len(create_call[1]["children"]) == 100
    # Remaining 50 appended separately
    assert mock_api.blocks.children.append.call_count == 1
    append_call = mock_api.blocks.children.append.call_args
    assert len(append_call[1]["children"]) == 50


def test_import_note_with_no_overflow_uses_single_call() -> None:
    """Notes with ≤100 blocks should create page+blocks in one API call, no append."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient, plain_text_span, paragraph_block

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.pages.create.return_value = {"id": "page-xyz", "url": "https://notion.so/p"}

    blocks = [paragraph_block([plain_text_span(f"B{i}")]) for i in range(50)]

    page_id = client.import_note_blocks(
        database_id="db-456",
        title="Small Note",
        tags=(),
        blocks=blocks,
    )

    assert page_id == "page-xyz"
    assert len(mock_api.pages.create.call_args[1]["children"]) == 50
    # No overflow append needed
    mock_api.blocks.children.append.assert_not_called()



# --- notion-import spec: Full note import orchestration with checkpointing ---


def test_execute_notion_operation_import_note_parses_and_creates_blocks(tmp_path) -> None:
    """import_note operation should parse the note file, build blocks, and call import_note_blocks."""
    from unittest.mock import MagicMock, patch
    from pathlib import Path
    from e2n.notion import NotionClient
    from e2n.state import OperationRecord
    from e2n.cli import _execute_notion_operation
    import json

    # Create a minimal extracted note file
    note_file = tmp_path / "note_000001.enex"
    note_file.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<en-export>\n'
        "<note><title>Test</title>"
        "<content><![CDATA[<?xml version=\"1.0\"?><en-note><p>Hello world</p></en-note>]]></content>"
        "</note>\n</en-export>\n",
        encoding="utf-8",
    )

    # Create a resource manifest (empty for this test)
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / "manifest.json").write_text("{}", encoding="utf-8")

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.pages.create.return_value = {"id": "created-page-id", "url": "https://notion.so/p"}

    operation = OperationRecord(
        operation_id=1,
        run_id="run-1",
        note_id="note_000001",
        operation_type="import_note",
        payload_json=json.dumps({
            "database_id": "db-123",
            "title": "Test",
            "tags": ["tag1"],
            "note_file": str(note_file),
            "resources_directory": str(resources_dir),
        }),
        idempotency_key="note_000001:import_note:abc",
        status="pending",
        attempt_count=0,
        next_retry_at=None,
    )

    result = _execute_notion_operation(client, operation)
    assert result == "created-page-id"
    # Verify pages.create was called with children (blocks from the parsed content)
    create_kwargs = mock_api.pages.create.call_args[1]
    assert "children" in create_kwargs
    assert len(create_kwargs["children"]) >= 1


def test_execute_notion_operation_import_note_uses_resource_manifest(tmp_path) -> None:
    """import_note should resolve resource hashes via manifest and include them as blocks."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient
    from e2n.state import OperationRecord
    from e2n.cli import _execute_notion_operation
    import json, hashlib, base64

    # Create a note with an en-media reference
    img_data = b"fake-image-bytes"
    img_hash = hashlib.md5(img_data).hexdigest()

    note_file = tmp_path / "note_000001.enex"
    note_file.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<en-export>\n'
        f'<note><title>With Image</title>'
        f'<content><![CDATA[<?xml version="1.0"?><en-note>'
        f'<en-media hash="{img_hash}" type="image/png"/>'
        f'</en-note>]]></content></note>\n</en-export>\n',
        encoding="utf-8",
    )

    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    img_file = resources_dir / "photo.png"
    img_file.write_bytes(img_data)
    manifest = {img_hash: str(img_file)}
    (resources_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    # upload_file mock
    mock_api.request.side_effect = [
        {"id": "upload-img-1", "status": "pending"},
        {"id": "upload-img-1", "status": "uploaded"},
    ]
    mock_api.pages.create.return_value = {"id": "page-with-img", "url": "https://notion.so/p"}

    operation = OperationRecord(
        operation_id=2,
        run_id="run-1",
        note_id="note_000001",
        operation_type="import_note",
        payload_json=json.dumps({
            "database_id": "db-456",
            "title": "With Image",
            "tags": [],
            "note_file": str(note_file),
            "resources_directory": str(resources_dir),
        }),
        idempotency_key="note_000001:import_note:xyz",
        status="pending",
        attempt_count=0,
        next_retry_at=None,
    )

    result = _execute_notion_operation(client, operation)
    assert result == "page-with-img"
    # Should have uploaded the file
    assert mock_api.request.call_count == 2



# --- exception-tracking spec: Runtime exception row creation + marker block_id capture ---


def test_create_exception_row_in_notion(monkeypatch) -> None:
    """Exception rows should be created in the Import-Exceptions database, not under import pages."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient, create_exception_row

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.pages.create.return_value = {"id": "exc-row-id", "url": "https://notion.so/exc"}

    row_id = create_exception_row(
        client,
        exception_database_id="exc-db-123",
        note_name="My Note",
        reasons=("Evernote Link",),
        error_message="Link requires manual resolution",
        source_file="Enduring.enex",
        link_text="Original Note",
        link_value="evernote:///view/123/s1/guid/guid/",
        page_url="https://notion.so/imported-page",
    )

    assert row_id == "exc-row-id"
    create_kwargs = mock_api.pages.create.call_args[1]
    # Must target the exception database, NOT an import database
    assert create_kwargs["parent"]["database_id"] == "exc-db-123"
    # Must have Reason multi-select
    props = create_kwargs["properties"]
    assert "Reason" in props


def test_import_note_captures_marker_block_ids() -> None:
    """When marker blocks are appended, their block_ids should be returned for state.db storage."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient, segments_to_notion_blocks
    from e2n.enml import ContentSegment

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api

    # Simulate pages.create returning the page with block children that include a callout
    mock_api.pages.create.return_value = {"id": "page-1", "url": "https://notion.so/p"}
    # blocks.children.list returns the appended blocks with their IDs
    mock_api.blocks.children.list.return_value = {
        "results": [
            {"id": "block-para-1", "type": "paragraph"},
            {"id": "block-callout-1", "type": "callout"},
        ],
        "has_more": False,
    }

    segments = [
        ContentSegment(kind="text", text="Hello"),
        ContentSegment(kind="evernote_link", text="Other Note", value="evernote:///view/1/s/g/g/"),
    ]
    blocks, exceptions = segments_to_notion_blocks(segments, {}, note_id="n1", note_title="Test")

    # The callout block is the marker — we need its block_id after creation
    assert len(exceptions) == 1
    # After import, caller should be able to list children and find callout block_ids
    children = client.list_block_children("page-1")
    callout_ids = [b["id"] for b in children if b["type"] == "callout"]
    assert callout_ids == ["block-callout-1"]


def test_delete_block_removes_marker() -> None:
    """NotionClient.delete_block should call DELETE on the Notion API."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.blocks.delete.return_value = {"id": "block-123", "archived": True}

    client.delete_block("block-123")

    mock_api.blocks.delete.assert_called_once_with(block_id="block-123")


def test_delete_block_handles_already_deleted() -> None:
    """If a marker block was already deleted (404), delete_block should not raise."""
    from unittest.mock import MagicMock
    from e2n.notion import NotionClient, NotionAPIError

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    mock_api.blocks.delete.side_effect = Exception("Could not find block")

    # Should not raise — graceful handling of already-deleted blocks
    client.delete_block("block-gone")



# --- exception-tracking: Wire exception row creation into import pipeline ---


def test_execute_import_note_creates_exception_rows_for_evernote_links(tmp_path) -> None:
    """When a note has evernote links, exception row Link should point to the BLOCK, not the page."""
    from unittest.mock import MagicMock, call
    from e2n.notion import NotionClient
    from e2n.state import OperationRecord
    from e2n.cli import _execute_notion_operation
    import json

    note_file = tmp_path / "note_000001.enex"
    note_file.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<en-export>\n'
        '<note><title>Link Note</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>'
        '<p>Before the link</p>'
        '<a href="evernote:///view/1/s/g/g/">Other Note</a>'
        '</en-note>]]></content></note>\n</en-export>\n',
        encoding="utf-8",
    )
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / "manifest.json").write_text("{}", encoding="utf-8")

    client = NotionClient.__new__(NotionClient)
    mock_api = MagicMock()
    client._sdk_client = mock_api
    # pages.create for note page, then pages.create for exception row
    mock_api.pages.create.side_effect = [
        {"id": "note-page-id", "url": "https://notion.so/note"},
        {"id": "exc-row-id", "url": "https://notion.so/exc"},
    ]
    # list_block_children returns the appended blocks with their IDs
    mock_api.blocks.children.list.return_value = {
        "results": [
            {"id": "blk-para-1", "type": "paragraph"},
            {"id": "blk-callout-1", "type": "callout"},
        ],
        "has_more": False,
    }

    operation = OperationRecord(
        operation_id=10,
        run_id="run-1",
        note_id="note_000001",
        operation_type="import_note",
        payload_json=json.dumps({
            "database_id": "db-import",
            "title": "Link Note",
            "tags": [],
            "note_file": str(note_file),
            "resources_directory": str(resources_dir),
            "exception_database_id": "exc-db-999",
        }),
        idempotency_key="note_000001:import_note:h1",
        status="pending",
        attempt_count=0,
        next_retry_at=None,
    )

    result = _execute_notion_operation(client, operation)
    assert result == "note-page-id"
    # Exception row should exist
    assert mock_api.pages.create.call_count == 2
    exc_kwargs = mock_api.pages.create.call_args_list[1][1]
    # Link must point to the BLOCK, not just the page
    link_url = exc_kwargs["properties"]["Link"]["url"]
    assert "blk-callout-1".replace("-", "") in link_url.replace("-", "") or "blk" in link_url, (
        f"Expected block-level URL, got: {link_url}"
    )
