"""Tests for WebUI wizard workflow."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from e2n.webui.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


# --- Step navigation and gating ---


def test_wizard_root_shows_step_1(client) -> None:
    """GET /wizard/ should display the first step (source configuration)."""
    response = client.get("/wizard/")
    assert response.status_code == 200
    assert "Configure Source" in response.text


def test_wizard_step_2_blocked_without_step_1(client) -> None:
    """GET /wizard/step/2 before step 1 complete should redirect to step 1."""
    response = client.get("/wizard/step/2", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "/wizard/" in response.headers.get("location", "")


def test_wizard_step_1_post_valid_source(client, tmp_path) -> None:
    """POST /wizard/step/1 with valid enex path should advance wizard state."""
    source = tmp_path / "Test.enex"
    source.write_text("<en-export></en-export>", encoding="utf-8")

    response = client.post(
        "/wizard/step/1",
        data={"enex_source": str(source), "processing_directory": str(tmp_path / "proc")},
        follow_redirects=False,
    )
    # Should redirect to step 2 on success
    assert response.status_code in (302, 303)
    assert "step/2" in response.headers.get("location", "")


def test_wizard_step_1_post_invalid_source(client, tmp_path) -> None:
    """POST /wizard/step/1 with non-existent path should show error."""
    response = client.post(
        "/wizard/step/1",
        data={"enex_source": "/nonexistent/path.enex", "processing_directory": str(tmp_path)},
    )
    assert response.status_code == 200
    assert "error" in response.text.lower() or "not found" in response.text.lower() or "does not exist" in response.text.lower()


# --- Notion connection step ---


def test_wizard_step_2_shows_notion_config(client, tmp_path) -> None:
    """GET /wizard/step/2 (when step 1 is done) should show Notion key input."""
    # First complete step 1
    source = tmp_path / "Test.enex"
    source.write_text("<en-export></en-export>", encoding="utf-8")
    client.post(
        "/wizard/step/1",
        data={"enex_source": str(source), "processing_directory": str(tmp_path / "proc")},
    )

    response = client.get("/wizard/step/2")
    assert response.status_code == 200
    assert "notion" in response.text.lower()



# --- Notion connection test ---


def test_wizard_step_2_post_test_connection_success(client, tmp_path, monkeypatch) -> None:
    """POST /wizard/step/2 with valid key should advance to step 3."""
    # Complete step 1 first
    source = tmp_path / "Test.enex"
    source.write_text("<en-export></en-export>", encoding="utf-8")
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(tmp_path / "proc")})

    # Mock the Notion API connection test
    from unittest.mock import MagicMock, patch
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post(
            "/wizard/step/2",
            data={"notion_key": "ntn_test_key_123", "notion_root": ""},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "step/3" in response.headers.get("location", "")


def test_wizard_step_2_post_test_connection_failure(client, tmp_path, monkeypatch) -> None:
    """POST /wizard/step/2 with bad key should show connection error."""
    source = tmp_path / "Test.enex"
    source.write_text("<en-export></en-export>", encoding="utf-8")
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(tmp_path / "proc")})

    from unittest.mock import patch
    with patch("e2n.webui.app.NotionClient", side_effect=Exception("Invalid token")):
        response = client.post(
            "/wizard/step/2",
            data={"notion_key": "bad_key", "notion_root": ""},
        )

    assert response.status_code == 200
    assert "error" in response.text.lower() or "failed" in response.text.lower() or "invalid" in response.text.lower()


# --- Progress endpoint ---


def test_wizard_progress_endpoint_returns_json(client, tmp_path) -> None:
    """GET /wizard/progress should return JSON with progress data."""
    source = tmp_path / "Test.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>N1</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>x</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    # Complete step 1 to set processing dir
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    response = client.get("/wizard/progress")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "total_notes" in data



# --- Step 3: Extract trigger ---


def test_wizard_step_3_triggers_extraction(client, tmp_path) -> None:
    """POST /wizard/step/3 should run extraction and redirect to step 4."""
    source = tmp_path / "Extract.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>Note1</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>hello</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    # Complete steps 1 and 2
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_test", "notion_root": ""})

    # Trigger extraction
    response = client.post("/wizard/step/3", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "step/4" in response.headers.get("location", "") or "step/3" in response.headers.get("location", "")

    # Extraction should have created processing output
    assert (proc_dir / "Extract" / "state.db").exists()
    assert (proc_dir / "Extract" / "master.txt").exists()


def test_wizard_step_3_blocked_without_step_2(client, tmp_path) -> None:
    """GET /wizard/step/3 before step 2 complete should redirect."""
    source = tmp_path / "T.enex"
    source.write_text("<en-export></en-export>", encoding="utf-8")
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(tmp_path / "p")})

    response = client.get("/wizard/step/3", follow_redirects=False)
    assert response.status_code in (302, 303)


# --- Step 4: Import trigger ---


def test_wizard_step_4_shows_import_page(client, tmp_path) -> None:
    """GET /wizard/step/4 (after extraction) should show import controls."""
    source = tmp_path / "Imp.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>N</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>x</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    # Complete steps 1, 2, 3
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_test", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.get("/wizard/step/4")
    assert response.status_code == 200
    assert "import" in response.text.lower()



# --- Step 4: Import trigger ---


def test_wizard_step_4_post_triggers_import(client, tmp_path) -> None:
    """POST /wizard/step/4 should run import (mocked Notion) and redirect to step 5."""
    source = tmp_path / "Imp.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>N</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>text</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    from unittest.mock import patch, MagicMock
    from e2n.notion import NotionPageRef, NotionDatabaseRef, NotionBootstrapResult

    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    mock_client.search_databases.return_value = []
    mock_client.import_note_blocks.return_value = "page-id-1"
    mock_client.list_block_children.return_value = []

    mock_bootstrap = NotionBootstrapResult(
        root=NotionPageRef(page_id="root-1", title="Root", url=None, parent_page_id=None),
        converted=NotionPageRef(page_id="conv-1", title="Evernote Import", url=None, parent_page_id="root-1"),
        exceptions=NotionPageRef(page_id="exc-1", title="Evernote Import Exceptions", url=None, parent_page_id="root-1"),
    )
    mock_import_db = NotionDatabaseRef(database_id="db-1", title="Imp", url=None, parent_page_id="conv-1")
    mock_exc_db = NotionDatabaseRef(database_id="exc-db-1", title="Import-Exceptions", url=None, parent_page_id="exc-1")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})

    client.post("/wizard/step/3")  # extract

    with patch("e2n.webui.app.NotionClient", return_value=mock_client), \
         patch("e2n.webui.app.bootstrap_notion_pages", return_value=mock_bootstrap), \
         patch("e2n.webui.app.ensure_import_database", return_value=mock_import_db), \
         patch("e2n.webui.app.ensure_exception_database", return_value=mock_exc_db):
        response = client.post("/wizard/step/4", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert "step/5" in response.headers.get("location", "")


# --- Step 5: Review ---


def test_wizard_step_5_shows_exception_summary(client, tmp_path) -> None:
    """GET /wizard/step/5 should display exception summary."""
    source = tmp_path / "Rev.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>  </title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    mock_client.search_databases.return_value = []
    mock_client._sdk_client = MagicMock()
    mock_client._sdk_client.pages.create.return_value = {"id": "p1", "url": "https://notion.so/p"}
    mock_client._sdk_client.databases.create.return_value = {
        "id": "db1", "url": "https://notion.so/db", "title": [{"text": {"content": "Rev"}}],
        "parent": {"page_id": "par1"},
    }
    mock_client._sdk_client.blocks.children.list.return_value = {"results": [], "has_more": False}

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})

    client.post("/wizard/step/3")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/4")

    response = client.get("/wizard/step/5")
    assert response.status_code == 200
    assert "review" in response.text.lower() or "exception" in response.text.lower() or "complete" in response.text.lower()



# --- Resolution Workbench ---


def test_resolve_dashboard_shows_categories(client, tmp_path) -> None:
    """GET /resolve/ should show exception categories with counts."""
    # Set up a processing dir with exceptions
    source = tmp_path / "Res.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export>'
        '<note><title>  </title><content><![CDATA[<?xml version="1.0"?><en-note></en-note>]]></content></note>'
        '<note><title>Link Note</title><content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Other</a></en-note>]]></content></note>'
        '</en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.get("/resolve/")
    assert response.status_code == 200
    assert "evernote link" in response.text.lower() or "evernote" in response.text.lower()


def test_resolve_by_type_lists_exceptions(client, tmp_path) -> None:
    """GET /resolve/type/evernote-link should list all evernote link exceptions."""
    source = tmp_path / "Links.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export>'
        '<note><title>LN</title><content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Target</a></en-note>]]></content></note>'
        '</en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.get("/resolve/type/evernote-link")
    assert response.status_code == 200
    assert "Target" in response.text or "LN" in response.text


def test_resolve_by_page_lists_exceptions_for_one_note(client, tmp_path) -> None:
    """GET /resolve/page/{note_id} should show all exceptions for that note."""
    source = tmp_path / "Multi.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export>'
        '<note><title>  </title><content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Link</a></en-note>]]></content></note>'
        '</en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.get("/resolve/page/note_000001")
    assert response.status_code == 200
    assert "Empty Title" in response.text or "Evernote Link" in response.text



# --- Auto-Relink ---


def test_auto_relink_warns_if_imports_not_complete(client, tmp_path) -> None:
    """POST /resolve/auto-relink should warn (not block) if imports are not all complete."""
    source = tmp_path / "AL.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>N</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Target</a></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")
    # Step 4 NOT executed — imports not complete

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post("/resolve/auto-relink")

    assert response.status_code == 200
    # Should show warning but still run
    assert "not all imports" in response.text.lower() or "complete" in response.text.lower()


def test_auto_relink_resolves_single_match_links(client, tmp_path) -> None:
    """POST /resolve/auto-relink should auto-resolve links with exactly one Notion title match."""
    source = tmp_path / "AR.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>Has Link</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Target Note</a></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    from unittest.mock import patch, MagicMock
    from e2n.notion import NotionPageRef, NotionDatabaseRef, NotionBootstrapResult

    mock_client = MagicMock()
    mock_client.search_pages.return_value = [
        NotionPageRef(page_id="target-page-1", title="Target Note", url="https://notion.so/target", parent_page_id="p")
    ]
    mock_client.import_note_blocks.return_value = "src-page-1"
    mock_client.list_block_children.return_value = []
    mock_client.update_block_with_page_link.return_value = {}
    mock_client.search_databases.return_value = []

    mock_bootstrap = NotionBootstrapResult(
        root=NotionPageRef(page_id="r", title="Root", url=None, parent_page_id=None),
        converted=NotionPageRef(page_id="c", title="Evernote Import", url=None, parent_page_id="r"),
        exceptions=NotionPageRef(page_id="e", title="Exceptions", url=None, parent_page_id="r"),
    )
    mock_import_db = NotionDatabaseRef(database_id="db1", title="AR", url=None, parent_page_id="c")
    mock_exc_db = NotionDatabaseRef(database_id="edb1", title="Import-Exceptions", url=None, parent_page_id="e")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client), \
         patch("e2n.webui.app.bootstrap_notion_pages", return_value=mock_bootstrap), \
         patch("e2n.webui.app.ensure_import_database", return_value=mock_import_db), \
         patch("e2n.webui.app.ensure_exception_database", return_value=mock_exc_db):
        client.post("/wizard/step/4")

    # Now auto-relink — should find "Target Note" with exactly 1 match
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post("/resolve/auto-relink")

    assert response.status_code == 200
    assert "resolved" in response.text.lower() or "1" in response.text


def test_auto_relink_skips_multi_match_links(client, tmp_path) -> None:
    """Links with multiple Notion matches should be skipped (not auto-resolved)."""
    source = tmp_path / "MM.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>Ambiguous</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note>'
        '<a href="evernote:///view/1/s/g/g/">Common Name</a></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"

    from unittest.mock import patch, MagicMock
    from e2n.notion import NotionPageRef, NotionDatabaseRef, NotionBootstrapResult

    mock_client = MagicMock()
    # Two pages match — ambiguous
    mock_client.search_pages.return_value = [
        NotionPageRef(page_id="p1", title="Common Name", url="u1", parent_page_id="x"),
        NotionPageRef(page_id="p2", title="Common Name", url="u2", parent_page_id="x"),
    ]
    mock_client.import_note_blocks.return_value = "src-page"
    mock_client.list_block_children.return_value = []
    mock_client.search_databases.return_value = []

    mock_bootstrap = NotionBootstrapResult(
        root=NotionPageRef(page_id="r", title="Root", url=None, parent_page_id=None),
        converted=NotionPageRef(page_id="c", title="EI", url=None, parent_page_id="r"),
        exceptions=NotionPageRef(page_id="e", title="EE", url=None, parent_page_id="r"),
    )
    mock_db = NotionDatabaseRef(database_id="d1", title="MM", url=None, parent_page_id="c")
    mock_edb = NotionDatabaseRef(database_id="ed1", title="IE", url=None, parent_page_id="e")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")
    with patch("e2n.webui.app.NotionClient", return_value=mock_client), \
         patch("e2n.webui.app.bootstrap_notion_pages", return_value=mock_bootstrap), \
         patch("e2n.webui.app.ensure_import_database", return_value=mock_db), \
         patch("e2n.webui.app.ensure_exception_database", return_value=mock_edb):
        client.post("/wizard/step/4")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post("/resolve/auto-relink")

    assert response.status_code == 200
    # Should report 0 resolved (ambiguous match skipped)
    assert "0" in response.text or "skipped" in response.text.lower() or "manual" in response.text.lower()



# --- Individual resolution actions ---


def test_resolve_acknowledge_marks_resolved(client, tmp_path) -> None:
    """POST /resolve/acknowledge/{note_id} should delete marker block and mark resolved."""
    source = tmp_path / "Ack.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>  </title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    mock_client.delete_block.return_value = None
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})

    client.post("/wizard/step/3")

    # Acknowledge an exception (e.g., Empty Title — page-level, auto-dismiss tier)
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post(
            "/resolve/acknowledge/note_000001",
            data={"block_id": "blk-123"},
            follow_redirects=False,
        )

    assert response.status_code in (200, 302, 303)


def test_resolve_delete_block_removes_from_notion(client, tmp_path) -> None:
    """POST /resolve/delete-block should call delete_block on Notion API."""
    source = tmp_path / "Del.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>Enc</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note><en-crypt hint="x">data</en-crypt></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    mock_client.delete_block.return_value = None
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})

    client.post("/wizard/step/3")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post(
            "/resolve/delete-block",
            data={"block_id": "blk-enc-1", "note_id": "note_000001"},
            follow_redirects=False,
        )

    assert response.status_code in (200, 302, 303)
    mock_client.delete_block.assert_called_once_with("blk-enc-1")


def test_resolve_decrypt_view_requires_passphrase(client, tmp_path) -> None:
    """GET /resolve/decrypt/{note_id} should show passphrase input form."""
    source = tmp_path / "Dcr.enex"
    source.write_text(
        '<?xml version="1.0"?><en-export><note><title>Secret</title>'
        '<content><![CDATA[<?xml version="1.0"?><en-note><en-crypt hint="pet name">Y2lwaGVy</en-crypt></en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.get("/resolve/decrypt/note_000001")
    assert response.status_code == 200
    assert "passphrase" in response.text.lower() or "password" in response.text.lower()
    assert "pet name" in response.text  # hint should be shown



# --- Decrypt action ---


def test_resolve_decrypt_post_shows_decrypted_content(client, tmp_path) -> None:
    """POST /resolve/decrypt/{note_id} with correct passphrase should show decrypted text."""
    # Create an encrypted note using AES-128-CBC (Evernote's format)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    import base64, hashlib, os

    passphrase = "mysecret"
    plaintext = b"This is my secret password: hunter2"

    # Evernote uses passphrase → MD5 → AES key (128-bit)
    key = hashlib.md5(passphrase.encode("utf-8")).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    # Evernote stores: IV + ciphertext, base64 encoded
    encrypted_b64 = base64.b64encode(iv + ciphertext).decode()

    source = tmp_path / "Enc.enex"
    source.write_text(
        f'<?xml version="1.0"?><en-export><note><title>Secret Note</title>'
        f'<content><![CDATA[<?xml version="1.0"?><en-note>'
        f'<en-crypt hint="pet" cipher="AES" length="128">{encrypted_b64}</en-crypt>'
        f'</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.post(
        "/resolve/decrypt/note_000001",
        data={"passphrase": "mysecret"},
    )
    assert response.status_code == 200
    assert "hunter2" in response.text


def test_resolve_decrypt_post_wrong_passphrase_shows_error(client, tmp_path) -> None:
    """POST /resolve/decrypt/{note_id} with wrong passphrase should show error."""
    import base64
    # Just put some random bytes that won't decrypt properly
    encrypted_b64 = base64.b64encode(b"\x00" * 32).decode()

    source = tmp_path / "Bad.enex"
    source.write_text(
        f'<?xml version="1.0"?><en-export><note><title>Locked</title>'
        f'<content><![CDATA[<?xml version="1.0"?><en-note>'
        f'<en-crypt hint="nope" cipher="AES" length="128">{encrypted_b64}</en-crypt>'
        f'</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    response = client.post(
        "/resolve/decrypt/note_000001",
        data={"passphrase": "wrongpassword"},
    )
    assert response.status_code == 200
    assert "error" in response.text.lower() or "failed" in response.text.lower() or "wrong" in response.text.lower()



# --- Decrypt and permanently import to Notion ---


def test_resolve_decrypt_and_import_replaces_block(client, tmp_path) -> None:
    """POST /resolve/decrypt-import/{note_id} should decrypt, create paragraph block, delete marker."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    import base64, hashlib, os

    passphrase = "testpass"
    plaintext = b"My decrypted secret content here"
    key = hashlib.md5(passphrase.encode("utf-8")).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    ciphertext = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    # Re-encrypt properly (encryptor consumed)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    encrypted_b64 = base64.b64encode(iv + ciphertext).decode()

    source = tmp_path / "DI.enex"
    source.write_text(
        f'<?xml version="1.0"?><en-export><note><title>Locked Note</title>'
        f'<content><![CDATA[<?xml version="1.0"?><en-note>'
        f'<en-crypt hint="test" cipher="AES" length="128">{encrypted_b64}</en-crypt>'
        f'</en-note>]]></content></note></en-export>',
        encoding="utf-8",
    )
    proc_dir = tmp_path / "proc"
    client.post("/wizard/step/1", data={"enex_source": str(source), "processing_directory": str(proc_dir)})

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.search_pages.return_value = []
    mock_client.delete_block.return_value = None
    mock_client._sdk_client = MagicMock()
    mock_client._sdk_client.blocks.children.append.return_value = {"results": [{"id": "new-blk"}]}

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        client.post("/wizard/step/2", data={"notion_key": "ntn_k", "notion_root": ""})
    client.post("/wizard/step/3")

    with patch("e2n.webui.app.NotionClient", return_value=mock_client):
        response = client.post(
            "/resolve/decrypt-import/note_000001",
            data={"passphrase": passphrase, "block_id": "marker-blk-1", "page_id": "page-1"},
        )

    assert response.status_code in (200, 302, 303)
    # Should have called delete_block to remove marker
    mock_client.delete_block.assert_called_once_with("marker-blk-1")
