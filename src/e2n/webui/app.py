"""FastAPI application for local e2n operations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from e2n.cli import run_notion_import
from e2n.enex import extract_enex_notes
from e2n.notion import (
    NotionClient,
    bootstrap_notion_pages,
    ensure_import_database,
    ensure_exception_database,
)
from e2n.state import ProcessingStateStore

try:
    from notion_client import Client as _NotionSDKClient
except ImportError:
    _NotionSDKClient = None  # type: ignore[assignment, misc]


@dataclass(frozen=True)
class RunCard:
    """Dashboard summary for one source processing directory."""

    source_name: str
    output_directory: str
    state_path: str
    latest_run_id: str | None
    note_count: int
    extracted_count: int
    extraction_error_count: int
    committed_count: int
    pending_count: int
    failed_count: int


def create_app() -> FastAPI:
    """Build and return the local web UI application."""
    app = FastAPI(title="e2n Local UI", version="0.1.0")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/extract")
    def extract(
        enex_source: str = Form(...),
        processing_dir: str = Form(...),
    ) -> RedirectResponse:
        try:
            source = Path(enex_source).expanduser().resolve()
            output_parent = Path(processing_dir).expanduser().resolve()
            if source.is_dir():
                sources = sorted(path for path in source.iterdir() if path.is_file() and path.suffix.lower() == ".enex")
                if not sources:
                    raise FileNotFoundError(f"No .enex files found in source directory: {source}")
                for enex_file in sources:
                    extract_enex_notes(enex_file, output_parent)
            else:
                extract_enex_notes(source, output_parent)
            return _redirect_with_message(processing_dir, "Extraction complete")
        except Exception as exc:
            return _redirect_with_error(processing_dir, str(exc))

    @app.post("/import")
    def import_notes(
        enex_source: str = Form(...),
        processing_dir: str = Form(...),
        notion_key: str = Form(...),
        notion_root: str = Form(""),
        resume: str | None = Form(None),
    ) -> RedirectResponse:
        try:
            args = _build_notion_import_args(
                enex_source=enex_source,
                processing_dir=processing_dir,
                notion_key=notion_key,
                notion_root=notion_root or None,
                resume=resume == "on",
            )
            run_notion_import(args)
            return _redirect_with_message(processing_dir, "Import execution complete")
        except Exception as exc:
            return _redirect_with_error(processing_dir, str(exc))

    @app.post("/run-control")
    def run_control(
        action: str = Form(...),
        run_id: str = Form(...),
        enex_source: str = Form(...),
        processing_dir: str = Form(...),
        notion_key: str = Form(""),
        notion_root: str = Form(""),
    ) -> RedirectResponse:
        try:
            if action not in {"reset", "wipe-local", "wipe-remote"}:
                raise ValueError(f"Unsupported action: {action}")
            args = _build_notion_import_args(
                enex_source=enex_source,
                processing_dir=processing_dir,
                notion_key=notion_key,
                notion_root=notion_root or None,
                resume=False,
                reset_run=run_id if action == "reset" else None,
                wipe_local=run_id if action == "wipe-local" else None,
                wipe_remote=run_id if action == "wipe-remote" else None,
            )
            run_notion_import(args)
            return _redirect_with_message(processing_dir, f"Run control completed: {action}")
        except Exception as exc:
            return _redirect_with_error(processing_dir, str(exc))

    # --- Wizard routes ---

    # In-memory wizard state (per-process; sufficient for single-user local tool)
    _wizard_state: dict[str, str] = {}

    @app.get("/wizard/", response_class=HTMLResponse)
    def wizard_root(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="wizard_step1.html",
            context={"error": ""},
        )

    @app.post("/wizard/step/1")
    def wizard_step_1_post(
        request: Request,
        enex_source: str = Form(...),
        processing_directory: str = Form(...),
    ):
        source_path = Path(enex_source).expanduser().resolve()
        if not source_path.exists():
            return templates.TemplateResponse(
                request=request,
                name="wizard_step1.html",
                context={"error": f"ENEX source does not exist: {source_path}"},
            )
        # Processing directory will be created automatically during extraction — no need to validate existence
        proc_path = Path(processing_directory).expanduser().resolve()
        proc_path.mkdir(parents=True, exist_ok=True)
        _wizard_state["enex_source"] = str(source_path)
        _wizard_state["processing_directory"] = str(proc_path)
        _wizard_state["step1_complete"] = "true"
        return RedirectResponse(url="/wizard/step/2", status_code=303)

    @app.get("/wizard/step/2", response_class=HTMLResponse)
    def wizard_step_2(request: Request):
        if _wizard_state.get("step1_complete") != "true":
            return RedirectResponse(url="/wizard/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="wizard_step2.html",
            context={"error": "", "success": ""},
        )

    @app.post("/wizard/step/2", response_class=HTMLResponse)
    def wizard_step_2_post(
        request: Request,
        notion_key: str = Form(...),
        notion_root: str = Form(""),
    ):
        if not notion_key.strip():
            return templates.TemplateResponse(
                request=request,
                name="wizard_step2.html",
                context={"error": "Notion key is required.", "success": ""},
            )
        if not notion_root.strip():
            return templates.TemplateResponse(
                request=request,
                name="wizard_step2.html",
                context={"error": "Notion root page is required.", "success": ""},
            )
        try:
            # Quick validation: lightweight API call with 10s timeout
            client = NotionClient(notion_key.strip())
            client.search_pages(notion_root.strip())
            _wizard_state["notion_key"] = notion_key.strip()
            _wizard_state["notion_root"] = notion_root.strip()
            _wizard_state["step2_complete"] = "true"
            return RedirectResponse(url="/wizard/step/3", status_code=303)
        except Exception as exc:
            error_msg = str(exc)
            if "unauthorized" in error_msg.lower() or "invalid" in error_msg.lower():
                error_msg = "Invalid API key. Check your integration secret at notion.so/my-integrations."
            elif "timeout" in error_msg.lower() or "connect" in error_msg.lower():
                error_msg = "Connection timed out. Check your internet connection."
            return templates.TemplateResponse(
                request=request,
                name="wizard_step2.html",
                context={"error": f"Connection failed: {error_msg}", "success": ""},
            )

    @app.get("/wizard/step/3", response_class=HTMLResponse)
    def wizard_step_3(request: Request):
        if _wizard_state.get("step2_complete") != "true":
            return RedirectResponse(url="/wizard/step/2", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="wizard_step3.html",
            context={"error": ""},
        )

    @app.post("/wizard/step/3")
    def wizard_step_3_post(request: Request):
        if _wizard_state.get("step2_complete") != "true":
            return RedirectResponse(url="/wizard/step/2", status_code=303)
        try:
            source = Path(_wizard_state["enex_source"])
            proc_dir = Path(_wizard_state["processing_directory"])
            result = extract_enex_notes(source, proc_dir)
            _wizard_state["step3_complete"] = "true"
            _wizard_state["extracted_count"] = str(result.total_notes)
            return RedirectResponse(url="/wizard/step/4", status_code=303)
        except Exception as exc:
            return templates.TemplateResponse(
                request=request,
                name="wizard_step3.html",
                context={"error": str(exc)},
            )

    @app.get("/wizard/step/4", response_class=HTMLResponse)
    def wizard_step_4(request: Request):
        if _wizard_state.get("step3_complete") != "true":
            return RedirectResponse(url="/wizard/step/3", status_code=303)
        count = _wizard_state.get("extracted_count", "0")
        return templates.TemplateResponse(
            request=request,
            name="wizard_step4.html",
            context={"error": "", "note_count": count},
        )

    @app.post("/wizard/step/4")
    def wizard_step_4_post(request: Request):
        if _wizard_state.get("step3_complete") != "true":
            return RedirectResponse(url="/wizard/step/3", status_code=303)
        try:
            source = Path(_wizard_state["enex_source"])
            proc_dir = Path(_wizard_state["processing_directory"])
            notion_key = _wizard_state.get("notion_key", "")
            notion_root = _wizard_state.get("notion_root") or None

            from e2n.enex import discover_enex_sources
            from e2n.enml import plan_enml_segments
            from e2n.notion import segments_to_notion_blocks
            import logging
            log = logging.getLogger("e2n.webui.import")

            client = NotionClient(notion_key)
            bootstrap = bootstrap_notion_pages(notion_key, root_title=notion_root)
            log.info("Bootstrap complete: converted=%s exceptions=%s", bootstrap.converted.page_id, bootstrap.exceptions.page_id)
            sources = discover_enex_sources(source)
            log.info("Sources: %s", [s.name for s in sources])

            imported_count = 0
            for src in sources:
                output_dir = proc_dir.expanduser().resolve() / src.stem
                state_path = output_dir / "state.db"
                if not state_path.exists():
                    log.warning("No state.db for source %s at %s — skipping", src.name, state_path)
                    continue
                import_db = ensure_import_database(client, bootstrap.converted.page_id, src.stem)
                log.info("Import DB: %s (%s)", import_db.title, import_db.database_id)
                exc_db = ensure_exception_database(client, bootstrap.exceptions.page_id)

                store = ProcessingStateStore(state_path)
                try:
                    run_id = store.latest_run_id()
                    if not run_id:
                        log.warning("No run_id in state.db for %s — skipping", src.name)
                        continue
                    notes = store.list_notes(run_id, status="extracted")
                    log.info("Found %d extracted notes for run %s", len(notes), run_id)

                    # Load resource manifest for this source
                    import json as _json
                    manifest_path = output_dir / "resources" / "manifest.json"
                    resource_manifest: dict[str, str] = {}
                    if manifest_path.exists():
                        resource_manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                    log.info("Resource manifest: %d entries", len(resource_manifest))
                    for note in notes:
                        note_file = output_dir / "notes" / f"{note.note_id}.enex"
                        if not note_file.exists():
                            log.warning("Note file missing: %s — skipping", note_file)
                            continue
                        try:
                            from lxml import etree
                            tree = etree.parse(str(note_file), parser=etree.XMLParser(recover=True))
                            root = tree.getroot()
                            note_el = root.find("note") if root.tag != "note" else root
                            content_el = note_el.find("content") if note_el is not None else None
                            content_text = content_el.text or "" if content_el is not None else ""

                            segments = plan_enml_segments(content_text)

                            # Upload resources referenced by this note
                            from pathlib import Path as _P
                            note_resource_map: dict[str, str] = {}
                            for seg in segments:
                                if seg.kind == "resource" and seg.value and seg.value in resource_manifest:
                                    local_path = _P(resource_manifest[seg.value])
                                    if local_path.exists() and seg.value not in note_resource_map:
                                        try:
                                            upload_id = client.upload_file(local_path, mime_type=seg.mime_type)
                                            note_resource_map[seg.value] = f"upload:{upload_id}"
                                        except Exception as upload_err:
                                            log.warning("Upload failed for %s: %s", local_path.name, upload_err)

                            blocks, exceptions = segments_to_notion_blocks(
                                segments, note_resource_map, note_id=note.note_id, note_title=note.title
                            )
                            page_id = client.import_note_blocks(
                                database_id=import_db.database_id,
                                title=note.title,
                                tags=tuple(note.tags),
                                blocks=blocks,
                            )
                            log.info("Imported note %s → page %s (%d blocks)", note.note_id, page_id, len(blocks))
                            imported_count += 1
                        except Exception as note_err:
                            log.error("Failed to import note %s (%s): %s", note.note_id, note.title, note_err)
                            continue

                        # Append import-time exceptions to exceptions.txt for unified tracking
                        if exceptions:
                            exc_file = output_dir / "exceptions.txt"
                            existing_keys = set()
                            if exc_file.exists():
                                for line in exc_file.read_text(encoding="utf-8").strip().splitlines():
                                    parts = line.split("\t")
                                    # Dedup key: note_id + reason + link_text
                                    if len(parts) >= 6:
                                        existing_keys.add(f"{parts[0]}:{parts[2]}:{parts[5]}")
                                    elif len(parts) >= 3:
                                        existing_keys.add(f"{parts[0]}:{parts[2]}:")
                            with exc_file.open("a", encoding="utf-8") as ef:
                                for exc in exceptions:
                                    reasons = ",".join(str(r) for r in (exc.reasons if hasattr(exc, "reasons") else ("Unsupported Content",)))
                                    error_msg = exc.error_comment if hasattr(exc, "error_comment") else getattr(exc, "marker_text", "")
                                    link_text = getattr(exc, "link_text", "")
                                    link_value = getattr(exc, "link_value", "")
                                    dedup_key = f"{note.note_id}:{reasons}:{link_text}"
                                    if dedup_key not in existing_keys:
                                        ef.write(f"{note.note_id}\t{note.title}\t{reasons}\t{src.name}\t\t{link_text}\t{link_value}\t{error_msg}\n")
                                        existing_keys.add(dedup_key)

                        # Create exception rows for any issues found
                        if exceptions:
                            from e2n.notion import create_exception_row
                            page_id_clean = page_id.replace("-", "")
                            marker_block_ids: list[str] = []
                            try:
                                children = client.list_block_children(page_id)
                                marker_block_ids = [b["id"] for b in children if b.get("type") == "callout"]
                            except Exception:
                                pass
                            for i, exc in enumerate(exceptions):
                                if i < len(marker_block_ids):
                                    block_id_clean = marker_block_ids[i].replace("-", "")
                                    exc_url = f"https://www.notion.so/{page_id_clean}#{block_id_clean}"
                                else:
                                    exc_url = f"https://www.notion.so/{page_id_clean}"
                                reasons = exc.reasons if hasattr(exc, "reasons") else ("Unsupported Content",)
                                error_msg = exc.error_comment if hasattr(exc, "error_comment") else getattr(exc, "marker_text", "")
                                # Detect encrypted content → use "Encrypted" reason
                                if "Encrypted content" in error_msg:
                                    reasons = ("Encrypted",)
                                try:
                                    create_exception_row(
                                        client,
                                        exception_database_id=exc_db.database_id,
                                        note_name=note.title,
                                        reasons=tuple(str(r) for r in reasons),
                                        error_message=error_msg,
                                        source_file=src.name,
                                        link_text=getattr(exc, "link_text", ""),
                                        link_value=getattr(exc, "link_value", ""),
                                        page_url=exc_url,
                                    )
                                except Exception as exc_err:
                                    log.warning("Could not create exception row: %s", exc_err)
                            log.info("Created %d exception row(s) for note %s", len(exceptions), note.note_id)
                finally:
                    store.close()

                # Create Notion rows for extraction-time exceptions (Empty Title, No Content)
                # These are in exceptions.txt from extraction but don't get import-time rows
                exc_file = output_dir / "exceptions.txt"
                if exc_file.exists():
                    from e2n.notion import create_exception_row as _create_ext_exc
                    import_time_notes: set[str] = set()  # notes that already got exception rows during import
                    for line in exc_file.read_text(encoding="utf-8").strip().splitlines():
                        parts = line.split("\t")
                        if len(parts) < 3:
                            continue
                        exc_note_id = parts[0]
                        exc_title = parts[1]
                        exc_reasons = parts[2]
                        # Skip if this is an import-time exception (Evernote Link, Unsupported, Encrypted)
                        # — those already have Notion rows from the import loop above
                        if "Evernote Link" in exc_reasons or "Unsupported Content" in exc_reasons or "Encrypted" in exc_reasons:
                            continue
                        # This is an extraction-time-only exception (Empty Title, No Content)
                        dedup_key = f"{exc_note_id}:{exc_reasons}"
                        if dedup_key in import_time_notes:
                            continue
                        import_time_notes.add(dedup_key)
                        # Find the page URL for this note
                        page_url = ""
                        try:
                            found = [p for p in client.search_pages(exc_title) if p.title == exc_title]
                            if found:
                                page_url = found[0].url or f"https://www.notion.so/{found[0].page_id.replace('-', '')}"
                        except Exception:
                            pass
                        try:
                            _create_ext_exc(
                                client,
                                exception_database_id=exc_db.database_id,
                                note_name=exc_title,
                                reasons=tuple(r.strip() for r in exc_reasons.split(",")),
                                source_file=src.name,
                                page_url=page_url,
                            )
                        except Exception as ext_err:
                            log.warning("Could not create extraction exception row for %s: %s", exc_title, ext_err)

            log.info("Import complete: %d notes imported", imported_count)
            _wizard_state["step4_complete"] = "true"
            return RedirectResponse(url="/wizard/step/5", status_code=303)
        except Exception as exc:
            count = _wizard_state.get("extracted_count", "0")
            return templates.TemplateResponse(
                request=request,
                name="wizard_step4.html",
                context={"error": str(exc), "note_count": count},
            )

    @app.get("/wizard/step/5", response_class=HTMLResponse)
    def wizard_step_5(request: Request):
        if _wizard_state.get("step4_complete") != "true" and _wizard_state.get("step3_complete") != "true":
            return RedirectResponse(url="/wizard/step/4", status_code=303)
        # Collect per-source summary from processing directories
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        sources_summary: list[dict] = []
        total_imported = 0
        total_exceptions = 0
        total_link_exceptions = 0
        total_encrypted = 0

        if proc_dir.exists():
            for child in sorted(proc_dir.iterdir()):
                if not child.is_dir():
                    continue
                # Count imported notes
                state_path = child / "state.db"
                imported = 0
                if state_path.exists():
                    store = ProcessingStateStore(state_path)
                    try:
                        run_id = store.latest_run_id()
                        if run_id:
                            notes = store.list_notes(run_id, status="extracted")
                            imported = len(notes)
                    finally:
                        store.close()

                # Count exceptions
                exc_count = 0
                exc_file = child / "exceptions.txt"
                if exc_file.exists():
                    lines = exc_file.read_text(encoding="utf-8").strip().splitlines()
                    exc_count = len(lines)
                    for line in lines:
                        parts = line.split("\t")
                        if len(parts) >= 3:
                            if "Evernote Link" in parts[2]:
                                total_link_exceptions += 1
                            if "Encrypted" in parts[2]:
                                total_encrypted += 1

                sources_summary.append({"name": child.name, "imported": imported, "exceptions": exc_count})
                total_imported += imported
                total_exceptions += exc_count

        return templates.TemplateResponse(
            request=request,
            name="wizard_step5.html",
            context={
                "sources": sources_summary,
                "total_imported": total_imported,
                "total_exceptions": total_exceptions,
                "total_link_exceptions": total_link_exceptions,
                "total_encrypted": total_encrypted,
            },
        )

    # --- Resolution Workbench routes ---

    def _load_exceptions_from_processing() -> list[dict]:
        """Load all exception records from processing directories."""
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        exceptions: list[dict] = []
        if not proc_dir.exists():
            return exceptions
        for child in proc_dir.iterdir():
            if not child.is_dir():
                continue
            exc_file = child / "exceptions.txt"
            if not exc_file.exists():
                continue
            for line in exc_file.read_text(encoding="utf-8").strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    exceptions.append({
                        "note_id": parts[0],
                        "title": parts[1],
                        "reasons": parts[2],
                        "source": parts[3] if len(parts) > 3 else "",
                        "block_url": parts[4] if len(parts) > 4 else "",
                        "link_text": parts[5] if len(parts) > 5 else "",
                        "link_value": parts[6] if len(parts) > 6 else "",
                        "error_message": parts[7] if len(parts) > 7 else "",
                    })
        return exceptions

    def _load_exceptions_from_notion() -> list[dict]:
        """Load open exceptions from the Notion Import-Exceptions database."""
        notion_key = _wizard_state.get("notion_key", "") or os.environ.get("NOTION_KEY", "") or os.environ.get("NOTION_TOKEN", "")
        notion_root = _wizard_state.get("notion_root", "") or os.environ.get("NOTION_ROOT", "")
        if not notion_key or not notion_root:
            return []
        try:
            client = NotionClient(notion_key)
            bootstrap_result = bootstrap_notion_pages(notion_key, root_title=notion_root)
            exc_db = ensure_exception_database(client, bootstrap_result.exceptions.page_id)
            # Query all rows from the exception database
            results = client._api(f"databases/{exc_db.database_id}/query", "POST", {})
            exceptions: list[dict] = []
            for page in results.get("results", []):
                props = page.get("properties", {})
                title_items = props.get("Note Name", {}).get("title", [])
                title = "".join(t.get("text", {}).get("content", "") for t in title_items)
                reason_items = props.get("Reason", {}).get("multi_select", [])
                reasons = ",".join(r.get("name", "") for r in reason_items)
                status_obj = props.get("Status", {}).get("select")
                status = status_obj.get("name", "") if status_obj else ""
                error_items = props.get("Error Message", {}).get("rich_text", [])
                error_msg = "".join(t.get("text", {}).get("content", "") for t in error_items)
                link_items = props.get("Linkable Text", {}).get("rich_text", [])
                link_text = "".join(t.get("text", {}).get("content", "") for t in link_items)
                link_url = props.get("Link", {}).get("url", "")

                # Only include Open exceptions
                if status == "Resolved":
                    continue

                exceptions.append({
                    "note_id": page["id"],
                    "title": title,
                    "reasons": reasons,
                    "error_message": error_msg,
                    "link_text": link_text,
                    "link_value": "",
                    "block_url": link_url,
                    "status": status,
                })
            return exceptions
        except Exception:
            return []

    @app.get("/resolve/", response_class=HTMLResponse)
    def resolve_dashboard(request: Request):
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        # Group by reason category
        categories: dict[str, int] = {}
        for exc in exceptions:
            for reason in exc["reasons"].split(","):
                reason = reason.strip()
                if reason:
                    categories[reason] = categories.get(reason, 0) + 1
        # Group by note for "by page" view — store title + count
        pages: dict[str, dict] = {}
        for exc in exceptions:
            nid = exc["note_id"]
            if nid not in pages:
                pages[nid] = {"title": exc["title"], "count": 0}
            pages[nid]["count"] += 1
        return templates.TemplateResponse(
            request=request,
            name="resolve_dashboard.html",
            context={"categories": categories, "pages": dict(sorted(pages.items(), key=lambda x: (-x[1]["count"], x[1]["title"]))), "total": len(exceptions)},
        )

    @app.get("/resolve/type/{reason_slug}", response_class=HTMLResponse)
    def resolve_by_type(request: Request, reason_slug: str):
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        # Map slug to reason (e.g., "evernote-link" → "Evernote Link")
        reason_map = {
            "evernote-link": "Evernote Link",
            "empty-title": "Empty Title",
            "no-content": "No Content",
            "unsupported-content": "Unsupported Content",
            "encrypted": "Encrypted",
        }
        target_reason = reason_map.get(reason_slug, reason_slug)
        filtered = [e for e in exceptions if target_reason in e["reasons"]]
        return templates.TemplateResponse(
            request=request,
            name="resolve_by_type.html",
            context={"exceptions": filtered, "reason": target_reason},
        )

    @app.get("/resolve/page/{note_id}", response_class=HTMLResponse)
    def resolve_by_page(request: Request, note_id: str):
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        filtered = [e for e in exceptions if e["note_id"] == note_id]
        return templates.TemplateResponse(
            request=request,
            name="resolve_by_page.html",
            context={"exceptions": filtered, "note_id": note_id},
        )

    @app.post("/resolve/auto-relink", response_class=HTMLResponse)
    def resolve_auto_relink(request: Request):
        # Warn (not block) if imports not complete
        warning = ""
        if _wizard_state.get("step4_complete") != "true":
            warning = "Not all imports are complete. Some links may not resolve until all sources are imported."

        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        link_exceptions = [e for e in exceptions if "Evernote Link" in e["reasons"]]

        import logging
        relink_log = logging.getLogger("e2n.webui.relink")
        relink_log.info("Auto-relink: found %d Evernote Link exceptions to process", len(link_exceptions))

        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key:
            return templates.TemplateResponse(
                request=request,
                name="resolve_auto_relink_result.html",
                context={"error": "No Notion key configured.", "warning": "",
                         "resolved": 0, "skipped": 0, "results": []},
            )

        client = NotionClient(notion_key)
        resolved = 0
        skipped = 0
        results: list[dict] = []

        for exc in link_exceptions:
            link_text = exc.get("link_text", "").strip()
            if not link_text:
                skipped += 1
                results.append({"title": exc["title"], "link_text": link_text, "status": "skipped", "reason": "no link text"})
                continue

            matches = [p for p in client.search_pages(link_text) if p.title == link_text]
            relink_log.info("  Link '%s': %d exact match(es)", link_text, len(matches))

            if len(matches) == 1:
                # Found exact match — attempt full resolution
                target_page = matches[0]
                resolution_success = False
                resolved_block_url = ""

                # Find the imported note page that contains this marker
                note_pages = [p for p in client.search_pages(exc["title"]) if p.title == exc["title"]]
                if note_pages:
                    note_page = note_pages[0]
                    page_id_clean = note_page.page_id.replace("-", "")
                    try:
                        # Find the callout block with this link text
                        children = client.list_block_children(note_page.page_id)
                        for block in children:
                            if block.get("type") == "callout":
                                block_text = "".join(
                                    rt.get("text", {}).get("content", "")
                                    for rt in block.get("callout", {}).get("rich_text", [])
                                )
                                if link_text in block_text:
                                    # Replace callout with inline link paragraph
                                    client.update_block_with_page_link(
                                        block["id"], link_text, target_page.url or f"https://notion.so/{target_page.page_id.replace('-', '')}"
                                    )
                                    # The replaced block keeps the same ID — it's now the resolved content
                                    block_id_clean = block["id"].replace("-", "")
                                    resolved_block_url = f"https://www.notion.so/{page_id_clean}#{block_id_clean}"
                                    resolution_success = True
                                    break
                    except Exception as resolve_err:
                        import logging
                        logging.getLogger("e2n.webui").warning("Resolution failed for %s: %s", link_text, resolve_err)

                # Update exception row: Status = Resolved, Link = resolved block
                if resolution_success and note_pages:
                    try:
                        all_matches_exc = client.search_pages(exc["title"])
                        for p in all_matches_exc:
                            if p.title == exc["title"]:
                                try:
                                    update_props: dict = {"Status": {"select": {"name": "Resolved"}}}
                                    if resolved_block_url:
                                        update_props["Link"] = {"url": resolved_block_url}
                                    client._sdk_call(
                                        client._sdk_client.pages.update,
                                        page_id=p.page_id,
                                        properties=update_props,
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass

                if resolution_success:
                    resolved += 1
                    results.append({"title": exc["title"], "link_text": link_text, "status": "resolved", "reason": f"→ {target_page.title}"})
                else:
                    resolved += 1
                    results.append({"title": exc["title"], "link_text": link_text, "status": "resolved", "reason": f"→ {target_page.title} (match found, block update may have failed)"})

            elif len(matches) == 0:
                skipped += 1
                results.append({"title": exc["title"], "link_text": link_text, "status": "skipped", "reason": "no match found"})
            else:
                skipped += 1
                results.append({"title": exc["title"], "link_text": link_text, "status": "skipped", "reason": f"{len(matches)} matches — manual review required"})

        relink_log.info("Auto-relink complete: resolved=%d, skipped=%d", resolved, skipped)
        return templates.TemplateResponse(
            request=request,
            name="resolve_auto_relink_result.html",
            context={"error": "", "warning": warning, "resolved": resolved, "skipped": skipped, "results": results},
        )

    # --- Individual resolution actions ---

    @app.post("/resolve/acknowledge/{note_id}")
    def resolve_acknowledge(request: Request, note_id: str, block_id: str = Form(default="")):
        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key:
            return RedirectResponse(url="/resolve/", status_code=303)

        client = NotionClient(notion_key)
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        note_exceptions = [e for e in exceptions if e["note_id"] == note_id]
        note_title = note_exceptions[0]["title"] if note_exceptions else ""

        # 1. Delete callout marker(s) from the imported page
        if block_id:
            client.delete_block(block_id)
        elif note_title:
            pages = [p for p in client.search_pages(note_title) if p.title == note_title]
            if pages:
                try:
                    children = client.list_block_children(pages[0].page_id)
                    for block in children:
                        if block.get("type") == "callout":
                            client.delete_block(block["id"])
                except Exception:
                    pass

        # 2. Update Import-Exceptions row(s) to Status = "Resolved" with Link to resolved content
        if note_title:
            try:
                # Build resolved link — points to the page (marker is deleted, page is clean)
                resolved_url = ""
                pages_found = [p for p in client.search_pages(note_title) if p.title == note_title]
                if pages_found:
                    resolved_url = pages_found[0].url or f"https://www.notion.so/{pages_found[0].page_id.replace('-', '')}"

                all_matches = client.search_pages(note_title)
                for p in all_matches:
                    if p.title == note_title:
                        try:
                            update_props: dict = {"Status": {"select": {"name": "Resolved"}}}
                            if resolved_url:
                                update_props["Link"] = {"url": resolved_url}
                            client._sdk_call(
                                client._sdk_client.pages.update,
                                page_id=p.page_id,
                                properties=update_props,
                            )
                        except Exception:
                            pass
            except Exception:
                pass

        return RedirectResponse(url="/resolve/", status_code=303)

    @app.post("/resolve/delete-block")
    def resolve_delete_block(request: Request, block_id: str = Form(default=""), note_id: str = Form(default="")):
        notion_key = _wizard_state.get("notion_key", "")
        if notion_key:
            client = NotionClient(notion_key)
            if block_id:
                client.delete_block(block_id)
            elif note_id:
                # Look up by note_id — find the page and delete callout blocks
                exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
                note_exc = [e for e in exceptions if e["note_id"] == note_id]
                note_title = note_exc[0]["title"] if note_exc else ""
                if note_title:
                    pages = [p for p in client.search_pages(note_title) if p.title == note_title]
                    if pages:
                        try:
                            children = client.list_block_children(pages[0].page_id)
                            for blk in children:
                                if blk.get("type") == "callout":
                                    client.delete_block(blk["id"])
                        except Exception:
                            pass
        return RedirectResponse(url="/resolve/", status_code=303)

    @app.get("/resolve/decrypt/{note_id}", response_class=HTMLResponse)
    def resolve_decrypt_view(request: Request, note_id: str):
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        note_exceptions = [e for e in exceptions if e["note_id"] == note_id]
        hint = ""
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        if proc_dir.exists():
            for child in proc_dir.iterdir():
                if not child.is_dir():
                    continue
                note_file = child / "notes" / f"{note_id}.enex"
                if note_file.exists():
                    from lxml import etree as _etree
                    tree = _etree.parse(str(note_file), parser=_etree.XMLParser(recover=True))
                    root = tree.getroot()
                    note_el = root.find("note") if root.tag != "note" else root
                    content_el = note_el.find("content") if note_el is not None else None
                    content_text = content_el.text or "" if content_el is not None else ""
                    if content_text:
                        try:
                            enml_root = _etree.fromstring(content_text.encode("utf-8"), parser=_etree.XMLParser(recover=True))
                            for crypt_el in enml_root.iter():
                                if crypt_el.tag == "en-crypt" or (crypt_el.tag and crypt_el.tag.endswith("en-crypt")):
                                    hint = crypt_el.attrib.get("hint", "")
                                    break
                        except Exception:
                            pass
                    break
        return templates.TemplateResponse(
            request=request,
            name="resolve_decrypt.html",
            context={"note_id": note_id, "hint": hint, "exceptions": note_exceptions},
        )

    @app.post("/resolve/decrypt/{note_id}", response_class=HTMLResponse)
    def resolve_decrypt_post(request: Request, note_id: str, passphrase: str = Form(...)):
        import re as _re
        import base64 as _b64
        import hashlib as _hashlib

        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        encrypted_b64 = ""
        hint = ""
        cipher_name = "AES"
        key_length = 128

        # Find the encrypted content in the note file
        if proc_dir.exists():
            for child in proc_dir.iterdir():
                if not child.is_dir():
                    continue
                note_file = child / "notes" / f"{note_id}.enex"
                if note_file.exists():
                    from lxml import etree as _etree
                    tree = _etree.parse(str(note_file), parser=_etree.XMLParser(recover=True))
                    root = tree.getroot()
                    note_el = root.find("note") if root.tag != "note" else root
                    content_el = note_el.find("content") if note_el is not None else None
                    content_text = content_el.text or "" if content_el is not None else ""
                    # Parse the ENML content to find en-crypt
                    if content_text:
                        try:
                            enml_root = _etree.fromstring(content_text.encode("utf-8"), parser=_etree.XMLParser(recover=True))
                            for crypt_el in enml_root.iter():
                                if crypt_el.tag == "en-crypt" or (crypt_el.tag and crypt_el.tag.endswith("en-crypt")):
                                    hint = crypt_el.attrib.get("hint", "")
                                    cipher_name = crypt_el.attrib.get("cipher", "AES")
                                    length_str = crypt_el.attrib.get("length", "128")
                                    key_length = int(length_str) if length_str.isdigit() else 128
                                    encrypted_b64 = (crypt_el.text or "").strip()
                                    break
                        except Exception:
                            pass
                    break

        if not encrypted_b64:
            return templates.TemplateResponse(
                request=request,
                name="resolve_decrypt_result.html",
                context={"note_id": note_id, "hint": hint, "error": "No encrypted content found.", "decrypted": ""},
            )

        # Attempt decryption — Evernote ENC0 format:
        # Bytes: "ENC0"(4) + salt(16) + salthmac(16) + IV(16) + ciphertext + HMAC(32)
        # Key: PBKDF2(passphrase, salt, 50000 iterations, SHA-256) → 128-bit key
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding, hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            import hmac as _hmac

            raw = _b64.b64decode(encrypted_b64)

            # Parse ENC0 format
            header = raw[0:4]  # b"ENC0"
            salt = raw[4:20]
            salthmac = raw[20:36]
            iv = raw[36:52]
            ciphertext = raw[52:-32]
            stored_hmac = raw[-32:]
            body = raw[0:-32]

            # Verify HMAC (confirms correct passphrase)
            kdf_hmac = PBKDF2HMAC(algorithm=hashes.SHA256(), length=key_length // 8, salt=salthmac, iterations=50000)
            key_hmac = kdf_hmac.derive(passphrase.encode("utf-8"))
            computed_hmac = _hmac.new(key_hmac, body, "sha256").digest()
            if not _hmac.compare_digest(computed_hmac, stored_hmac):
                return templates.TemplateResponse(
                    request=request,
                    name="resolve_decrypt_result.html",
                    context={"note_id": note_id, "hint": hint, "error": "Wrong passphrase.", "decrypted": ""},
                )

            # Derive decryption key
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=key_length // 8, salt=salt, iterations=50000)
            key = kdf.derive(passphrase.encode("utf-8"))

            # Decrypt
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()

            # Remove PKCS7 padding
            unpadder = padding.PKCS7(128).unpadder()
            decrypted_bytes = unpadder.update(padded) + unpadder.finalize()
            decrypted_text = decrypted_bytes.decode("utf-8")

            # Strip HTML tags — decrypted Evernote content is ENML/HTML
            import re as _strip_re
            decrypted_text = _strip_re.sub(r'<[^>]+>', '', decrypted_text).strip()

            # Look up the page and block_id for resolution actions
            page_id = ""
            block_id = ""
            notion_key = _wizard_state.get("notion_key", "")
            if notion_key:
                try:
                    resolve_client = NotionClient(notion_key)
                    exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
                    note_exc = [e for e in exceptions if e["note_id"] == note_id]
                    note_title = note_exc[0]["title"] if note_exc else ""
                    if note_title:
                        pages_found = [p for p in resolve_client.search_pages(note_title) if p.title == note_title]
                        if pages_found:
                            page_id = pages_found[0].page_id
                            children = resolve_client.list_block_children(page_id)
                            for blk in children:
                                if blk.get("type") == "callout":
                                    block_text = "".join(rt.get("text", {}).get("content", "") for rt in blk.get("callout", {}).get("rich_text", []))
                                    if "Encrypted" in block_text or "passphrase" in block_text:
                                        block_id = blk["id"]
                                        break
                except Exception:
                    pass

            return templates.TemplateResponse(
                request=request,
                name="resolve_decrypt_result.html",
                context={"note_id": note_id, "hint": hint, "error": "", "decrypted": decrypted_text, "passphrase": passphrase, "page_id": page_id, "block_id": block_id},
            )
        except Exception as exc:
            error_msg = str(exc)
            if "wrong passphrase" in error_msg.lower() or "padding" in error_msg.lower():
                error_msg = "Decryption failed — wrong passphrase or corrupted data."
            return templates.TemplateResponse(
                request=request,
                name="resolve_decrypt_result.html",
                context={"note_id": note_id, "hint": hint, "error": f"Decryption failed: {error_msg}", "decrypted": ""},
            )

    @app.post("/resolve/decrypt-import/{note_id}")
    def resolve_decrypt_import(
        request: Request,
        note_id: str,
        passphrase: str = Form(...),
        block_id: str = Form(""),
        page_id: str = Form(""),
    ):
        """Decrypt content, insert as paragraph block at marker position, delete marker."""
        import base64 as _b64
        import hashlib as _hashlib

        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        encrypted_b64 = ""
        key_length = 128

        if proc_dir.exists():
            for child in proc_dir.iterdir():
                if not child.is_dir():
                    continue
                note_file = child / "notes" / f"{note_id}.enex"
                if note_file.exists():
                    from lxml import etree as _etree
                    tree = _etree.parse(str(note_file), parser=_etree.XMLParser(recover=True))
                    root = tree.getroot()
                    note_el = root.find("note") if root.tag != "note" else root
                    content_el = note_el.find("content") if note_el is not None else None
                    content_text = content_el.text or "" if content_el is not None else ""
                    if content_text:
                        try:
                            enml_root = _etree.fromstring(content_text.encode("utf-8"), parser=_etree.XMLParser(recover=True))
                            for crypt_el in enml_root.iter():
                                if crypt_el.tag == "en-crypt" or (crypt_el.tag and crypt_el.tag.endswith("en-crypt")):
                                    length_str = crypt_el.attrib.get("length", "128")
                                    key_length = int(length_str) if length_str.isdigit() else 128
                                    encrypted_b64 = (crypt_el.text or "").strip()
                                    break
                        except Exception:
                            pass
                    break

        if not encrypted_b64:
            return RedirectResponse(url=f"/resolve/decrypt/{note_id}", status_code=303)

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding, hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            raw = _b64.b64decode(encrypted_b64)
            salt = raw[4:20]
            iv = raw[36:52]
            ciphertext = raw[52:-32]

            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=key_length // 8, salt=salt, iterations=50000)
            key = kdf.derive(passphrase.encode("utf-8"))

            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            decrypted_text = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
            # Strip HTML tags — decrypted Evernote content is ENML/HTML
            import re as _strip_re2
            decrypted_text = _strip_re2.sub(r'<[^>]+>', '', decrypted_text).strip()
        except Exception:
            return RedirectResponse(url=f"/resolve/decrypt/{note_id}", status_code=303)

        # Insert decrypted content as paragraph block, delete marker, mark resolved
        notion_key = _wizard_state.get("notion_key", "")
        if notion_key:
            from e2n.notion import paragraph_block, plain_text_span
            client = NotionClient(notion_key)

            # If we don't have page_id/block_id from form, look them up
            if not page_id or not block_id:
                exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
                note_exc = [e for e in exceptions if e["note_id"] == note_id]
                note_title = note_exc[0]["title"] if note_exc else ""
                if note_title:
                    pages_found = [p for p in client.search_pages(note_title) if p.title == note_title]
                    if pages_found:
                        page_id = pages_found[0].page_id
                        children = client.list_block_children(page_id)
                        for blk in children:
                            if blk.get("type") == "callout":
                                blk_text = "".join(rt.get("text", {}).get("content", "") for rt in blk.get("callout", {}).get("rich_text", []))
                                if "Encrypted" in blk_text or "passphrase" in blk_text:
                                    block_id = blk["id"]
                                    break

            if page_id and block_id:
                # Replace the callout with decrypted content (update block to paragraph)
                try:
                    client._sdk_call(
                        client._sdk_client.blocks.update,
                        block_id=block_id,
                        paragraph={"rich_text": [{"type": "text", "text": {"content": decrypted_text[:2000]}}]},
                    )
                except Exception:
                    # Fallback: append new block and delete old
                    block = paragraph_block([plain_text_span(decrypted_text[:2000])])
                    client._sdk_call(client._sdk_client.blocks.children.append, block_id=page_id, children=[block])
                    client.delete_block(block_id)

                # Mark exception row as Resolved
                page_id_clean = page_id.replace("-", "")
                block_id_clean = block_id.replace("-", "")
                resolved_url = f"https://www.notion.so/{page_id_clean}#{block_id_clean}"
                try:
                    exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
                    note_exc = [e for e in exceptions if e["note_id"] == note_id]
                    note_title = note_exc[0]["title"] if note_exc else ""
                    if note_title:
                        all_matches = client.search_pages(note_title)
                        for p in all_matches:
                            if p.title == note_title:
                                try:
                                    client._sdk_call(
                                        client._sdk_client.pages.update,
                                        page_id=p.page_id,
                                        properties={"Status": {"select": {"name": "Resolved"}}, "Link": {"url": resolved_url}},
                                    )
                                except Exception:
                                    pass
                except Exception:
                    pass

        return RedirectResponse(url="/resolve/", status_code=303)

    # --- Evernote Link Management (first-class feature) ---

    @app.get("/links/", response_class=HTMLResponse)
    def links_home(request: Request):
        """Show unique link targets and how many exceptions reference each."""
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        link_exceptions = [e for e in exceptions if "Evernote Link" in e["reasons"]]
        # Group by link_text (the target page name)
        targets: dict[str, int] = {}
        for exc in link_exceptions:
            lt = exc.get("link_text", "").strip()
            if lt:
                targets[lt] = targets.get(lt, 0) + 1
        # Sort by count desc, then alpha
        sorted_targets = sorted(targets.items(), key=lambda x: (-x[1], x[0]))
        return templates.TemplateResponse(
            request=request,
            name="links.html",
            context={"targets": sorted_targets, "total_links": len(link_exceptions), "total_targets": len(targets)},
        )

    @app.post("/links/resolve-all", response_class=HTMLResponse)
    def links_resolve_all(request: Request):
        """Auto-resolve all link targets from most-referenced to least."""
        import logging
        link_log = logging.getLogger("e2n.webui.links")

        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key:
            return templates.TemplateResponse(
                request=request, name="links_result.html",
                context={"error": "No Notion key configured.", "page_name": "ALL", "resolved": 0, "failed": 0, "results": []},
            )

        client = NotionClient(notion_key)
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        link_exceptions = [e for e in exceptions if "Evernote Link" in e["reasons"]]

        # Group by target and sort by count desc
        targets: dict[str, list] = {}
        for exc in link_exceptions:
            lt = exc.get("link_text", "").strip()
            if lt:
                targets.setdefault(lt, []).append(exc)
        sorted_targets = sorted(targets.items(), key=lambda x: -len(x[1]))

        total_resolved = 0
        total_failed = 0
        results: list[dict] = []

        for page_name, refs in sorted_targets:
            # Find target page
            target_matches = [p for p in client.search_pages(page_name) if p.title == page_name]
            if not target_matches:
                total_failed += len(refs)
                results.append({"title": page_name, "status": "skipped", "reason": f"page not found ({len(refs)} refs)"})
                continue

            target_page = target_matches[0]
            target_url = target_page.url or f"https://www.notion.so/{target_page.page_id.replace('-', '')}"

            resolved_this = 0
            for exc in refs:
                note_title = exc["title"]
                try:
                    note_pages = [p for p in client.search_pages(note_title) if p.title == note_title]
                    if not note_pages:
                        total_failed += 1
                        continue
                    children = client.list_block_children(note_pages[0].page_id)
                    for block in children:
                        if block.get("type") == "callout":
                            block_text = "".join(rt.get("text", {}).get("content", "") for rt in block.get("callout", {}).get("rich_text", []))
                            if page_name in block_text:
                                client.update_block_with_page_link(block["id"], page_name, target_url)
                                resolved_this += 1
                                total_resolved += 1
                                break
                except Exception:
                    total_failed += 1

            results.append({"title": page_name, "status": "resolved" if resolved_this > 0 else "partial", "reason": f"resolved {resolved_this}/{len(refs)}"})
            link_log.info("  %s: resolved %d/%d", page_name, resolved_this, len(refs))

        link_log.info("Resolve-all complete: resolved=%d, failed=%d", total_resolved, total_failed)
        return templates.TemplateResponse(
            request=request, name="links_result.html",
            context={"error": "", "page_name": "ALL LINKS", "resolved": total_resolved, "failed": total_failed, "results": results},
        )

    @app.post("/links/resolve", response_class=HTMLResponse)
    def links_resolve(request: Request, page_name: str = Form(...), search_source: str = Form("Evernote Import"), override_target: str = Form("")):
        """Resolve all Evernote Link exceptions that reference a given page name."""
        import logging
        link_log = logging.getLogger("e2n.webui.links")

        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key:
            return templates.TemplateResponse(
                request=request, name="links_result.html",
                context={"error": "No Notion key configured.", "page_name": page_name, "resolved": 0, "failed": 0, "results": []},
            )

        client = NotionClient(notion_key)

        # Step 1: Find the target page — use override if provided (for orphan links)
        search_name = override_target.strip() if override_target.strip() else page_name
        target_matches = [p for p in client.search_pages(search_name) if p.title == search_name]
        if not target_matches:
            return templates.TemplateResponse(
                request=request, name="links_result.html",
                context={"error": f"Page '{search_name}' not found in Notion.", "page_name": page_name, "resolved": 0, "failed": 0, "results": []},
            )
        target_page = target_matches[0]
        target_url = target_page.url or f"https://www.notion.so/{target_page.page_id.replace('-', '')}"
        link_log.info("Target page found: %s (%s)", page_name, target_page.page_id)

        # Step 2: Find all exception records referencing this page name
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        referencing = [e for e in exceptions if "Evernote Link" in e["reasons"] and e.get("link_text", "").strip() == page_name]
        link_log.info("Found %d exceptions referencing '%s'", len(referencing), page_name)

        # Step 3: For each referencing note, replace the callout with an inline link
        resolved = 0
        failed = 0
        results: list[dict] = []

        for exc in referencing:
            note_title = exc["title"]
            try:
                # Find the referencing note's page
                note_pages = [p for p in client.search_pages(note_title) if p.title == note_title]
                if not note_pages:
                    failed += 1
                    results.append({"title": note_title, "status": "failed", "reason": "page not found"})
                    continue

                note_page = note_pages[0]
                # Find the callout block containing this link text
                children = client.list_block_children(note_page.page_id)
                replaced = False
                for block in children:
                    if block.get("type") == "callout":
                        block_text = "".join(
                            rt.get("text", {}).get("content", "") for rt in block.get("callout", {}).get("rich_text", [])
                        )
                        if page_name in block_text:
                            # Replace callout with inline link
                            client.update_block_with_page_link(block["id"], page_name, target_url)
                            # Update exception row: Status=Resolved, Link=new block URL
                            block_id_clean = block["id"].replace("-", "")
                            page_id_clean = note_page.page_id.replace("-", "")
                            new_link = f"https://www.notion.so/{page_id_clean}#{block_id_clean}"
                            # Find and update exception row in Notion
                            try:
                                all_exc_matches = client.search_pages(note_title)
                                for p in all_exc_matches:
                                    if p.title == note_title:
                                        try:
                                            client._sdk_call(
                                                client._sdk_client.pages.update,
                                                page_id=p.page_id,
                                                properties={"Status": {"select": {"name": "Resolved"}}, "Link": {"url": new_link}},
                                            )
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            replaced = True
                            resolved += 1
                            results.append({"title": note_title, "status": "resolved", "reason": f"→ linked to {page_name}"})
                            break

                if not replaced:
                    failed += 1
                    results.append({"title": note_title, "status": "failed", "reason": "callout block not found on page"})

            except Exception as exc_err:
                failed += 1
                results.append({"title": note_title, "status": "failed", "reason": str(exc_err)[:100]})

        link_log.info("Link resolution complete: resolved=%d, failed=%d", resolved, failed)
        return templates.TemplateResponse(
            request=request, name="links_result.html",
            context={"error": "", "page_name": page_name, "resolved": resolved, "failed": failed, "results": results},
        )

    # --- Trivial resolution routes ---

    @app.get("/resolve/passwords", response_class=HTMLResponse)
    def resolve_passwords(request: Request):
        """List all encrypted exceptions for batch password management."""
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        encrypted = [e for e in exceptions if "Encrypted" in e["reasons"] or "Encrypted" in e.get("error_message", "")]
        return templates.TemplateResponse(
            request=request,
            name="resolve_passwords.html",
            context={"exceptions": encrypted, "total": len(encrypted)},
        )

    @app.get("/passwords/", response_class=HTMLResponse)
    def passwords_home(request: Request):
        """First-class password management — lists all encrypted items from Import-Exceptions."""
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        encrypted = [e for e in exceptions if "Encrypted" in e["reasons"] or "Encrypted" in e.get("error_message", "")]
        return templates.TemplateResponse(
            request=request,
            name="passwords.html",
            context={"exceptions": encrypted, "total": len(encrypted)},
        )

    @app.get("/passwords/decrypt/{note_id}", response_class=HTMLResponse)
    def passwords_decrypt(request: Request, note_id: str):
        """Decrypt a single password — opens in new tab for easy copy."""
        # Reuse the existing decrypt view logic
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        note_exceptions = [e for e in exceptions if e["note_id"] == note_id]
        hint = ""
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        if proc_dir.exists():
            for child in proc_dir.iterdir():
                if not child.is_dir():
                    continue
                note_file = child / "notes" / f"{note_id}.enex"
                if note_file.exists():
                    from lxml import etree as _etree
                    tree = _etree.parse(str(note_file), parser=_etree.XMLParser(recover=True))
                    root = tree.getroot()
                    note_el = root.find("note") if root.tag != "note" else root
                    content_el = note_el.find("content") if note_el is not None else None
                    content_text = content_el.text or "" if content_el is not None else ""
                    if content_text:
                        try:
                            enml_root = _etree.fromstring(content_text.encode("utf-8"), parser=_etree.XMLParser(recover=True))
                            for crypt_el in enml_root.iter():
                                if crypt_el.tag == "en-crypt" or (crypt_el.tag and crypt_el.tag.endswith("en-crypt")):
                                    hint = crypt_el.attrib.get("hint", "")
                                    break
                        except Exception:
                            pass
                    break
        title = note_exceptions[0]["title"] if note_exceptions else note_id
        return templates.TemplateResponse(
            request=request,
            name="passwords_decrypt.html",
            context={"note_id": note_id, "hint": hint, "title": title},
        )

    @app.post("/passwords/decrypt/{note_id}", response_class=HTMLResponse)
    def passwords_decrypt_post(request: Request, note_id: str, passphrase: str = Form(...)):
        """Decrypt and show password content in a minimal view for copying."""
        import base64 as _b64
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        encrypted_b64 = ""
        key_length = 128

        if proc_dir.exists():
            for child in proc_dir.iterdir():
                if not child.is_dir():
                    continue
                note_file = child / "notes" / f"{note_id}.enex"
                if note_file.exists():
                    from lxml import etree as _etree
                    tree = _etree.parse(str(note_file), parser=_etree.XMLParser(recover=True))
                    root = tree.getroot()
                    note_el = root.find("note") if root.tag != "note" else root
                    content_el = note_el.find("content") if note_el is not None else None
                    content_text = content_el.text or "" if content_el is not None else ""
                    if content_text:
                        try:
                            enml_root = _etree.fromstring(content_text.encode("utf-8"), parser=_etree.XMLParser(recover=True))
                            for crypt_el in enml_root.iter():
                                if crypt_el.tag == "en-crypt" or (crypt_el.tag and crypt_el.tag.endswith("en-crypt")):
                                    length_str = crypt_el.attrib.get("length", "128")
                                    key_length = int(length_str) if length_str.isdigit() else 128
                                    encrypted_b64 = (crypt_el.text or "").strip()
                                    break
                        except Exception:
                            pass
                    break

        if not encrypted_b64:
            return templates.TemplateResponse(request=request, name="passwords_result.html", context={"error": "No encrypted content found.", "decrypted": "", "title": note_id})

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding, hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            import hmac as _hmac

            raw = _b64.b64decode(encrypted_b64)
            salt = raw[4:20]
            salthmac = raw[20:36]
            iv = raw[36:52]
            ciphertext = raw[52:-32]
            stored_hmac = raw[-32:]
            body = raw[0:-32]

            kdf_hmac = PBKDF2HMAC(algorithm=hashes.SHA256(), length=key_length // 8, salt=salthmac, iterations=50000)
            key_hmac = kdf_hmac.derive(passphrase.encode("utf-8"))
            computed_hmac = _hmac.new(key_hmac, body, "sha256").digest()
            if not _hmac.compare_digest(computed_hmac, stored_hmac):
                return templates.TemplateResponse(request=request, name="passwords_result.html", context={"error": "Wrong passphrase.", "decrypted": "", "title": note_id})

            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=key_length // 8, salt=salt, iterations=50000)
            key = kdf.derive(passphrase.encode("utf-8"))
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            decrypted_text = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
            import re as _strip_re3
            decrypted_text = _strip_re3.sub(r'<[^>]+>', '', decrypted_text).strip()

            exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
            note_exc = [e for e in exceptions if e["note_id"] == note_id]
            title = note_exc[0]["title"] if note_exc else note_id
            return templates.TemplateResponse(request=request, name="passwords_result.html", context={"error": "", "decrypted": decrypted_text, "title": title})
        except Exception as exc:
            return templates.TemplateResponse(request=request, name="passwords_result.html", context={"error": f"Decryption failed: {exc}", "decrypted": "", "title": note_id})

    @app.post("/resolve/delete-empty-pages")
    def resolve_delete_empty_pages(request: Request):
        """Batch delete all empty pages (No Content exceptions) from Notion."""
        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key:
            return RedirectResponse(url="/resolve/", status_code=303)
        client = NotionClient(notion_key)
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        empty = [e for e in exceptions if "No Content" in e["reasons"]]
        deleted = 0
        for exc in empty:
            pages = [p for p in client.search_pages(exc["title"]) if p.title == exc["title"]]
            if pages:
                try:
                    client.archive_page(pages[0].page_id)
                    # Update exception row: Resolved, Link cleared (page deleted)
                    all_matches = client.search_pages(exc["title"])
                    for p in all_matches:
                        if p.title == exc["title"]:
                            try:
                                client._sdk_call(
                                    client._sdk_client.pages.update,
                                    page_id=p.page_id,
                                    properties={"Status": {"select": {"name": "Resolved"}}, "Link": {"url": None}},
                                )
                            except Exception:
                                pass
                    deleted += 1
                except Exception:
                    pass
        return templates.TemplateResponse(
            request=request,
            name="resolve_auto_relink_result.html",
            context={"error": "", "warning": "", "resolved": deleted, "skipped": len(empty) - deleted,
                     "results": [{"title": e["title"], "link_text": "", "status": "deleted", "reason": "empty page removed"} for e in empty[:deleted]]},
        )

    @app.post("/resolve/rename-page")
    def resolve_rename_page(request: Request, note_id: str = Form(""), new_title: str = Form("")):
        """Rename an 'Empty Title' page in Notion."""
        notion_key = _wizard_state.get("notion_key", "")
        if not notion_key or not new_title.strip():
            return RedirectResponse(url="/resolve/", status_code=303)
        client = NotionClient(notion_key)
        # Find the page with "Empty Title"
        pages = [p for p in client.search_pages("Empty Title") if p.title == "Empty Title"]
        if pages:
            try:
                client._sdk_call(
                    client._sdk_client.pages.update,
                    page_id=pages[0].page_id,
                    properties={"Name": {"title": [{"text": {"content": new_title.strip()}}]}},
                )
            except Exception:
                pass
        return RedirectResponse(url="/resolve/", status_code=303)

    @app.get("/wizard/status", response_class=HTMLResponse)
    def wizard_status(request: Request):
        proc_dir = Path(_wizard_state.get("processing_directory", "")).expanduser().resolve()
        notes_extracted = int(_wizard_state.get("extracted_count", "0"))
        step = "Not started"
        if _wizard_state.get("step4_complete") == "true":
            step = "Import complete — review exceptions"
        elif _wizard_state.get("step3_complete") == "true":
            step = "Extraction complete — ready to import"
        elif _wizard_state.get("step2_complete") == "true":
            step = "Connected to Notion — ready to extract"
        elif _wizard_state.get("step1_complete") == "true":
            step = "Source configured — connecting to Notion"
        exceptions = _load_exceptions_from_notion() or _load_exceptions_from_processing()
        return templates.TemplateResponse(
            request=request,
            name="wizard_status.html",
            context={
                "step": step,
                "notes_extracted": notes_extracted,
                "exception_count": len(exceptions),
                "source": _wizard_state.get("enex_source", ""),
                "processing_dir": _wizard_state.get("processing_directory", ""),
            },
        )

    @app.get("/wizard/progress")
    def wizard_progress():
        proc_dir = _wizard_state.get("processing_directory", "")
        if not proc_dir:
            return {"status": "not_started", "total_notes": 0, "processed": 0, "current": ""}
        proc_path = Path(proc_dir).expanduser().resolve()
        # Scan for state.db files in processing subdirectories
        total = 0
        processed = 0
        for child in proc_path.iterdir() if proc_path.exists() else []:
            state_path = child / "state.db" if child.is_dir() else None
            if state_path and state_path.exists():
                store = ProcessingStateStore(state_path)
                try:
                    run_id = store.latest_run_id()
                    if run_id:
                        counts = store.count_operations_by_status(run_id)
                        total += counts.get("pending", 0) + counts.get("committed", 0) + counts.get("failed", 0)
                        processed += counts.get("committed", 0)
                finally:
                    store.close()
        status = "complete" if total > 0 and processed == total else "in_progress" if processed > 0 else "not_started"
        return {"status": status, "total_notes": total, "processed": processed, "current": ""}

    return app


def _build_notion_import_args(
    enex_source: str,
    processing_dir: str,
    notion_key: str,
    notion_root: str | None,
    resume: bool,
    reset_run: str | None = None,
    wipe_local: str | None = None,
    wipe_remote: str | None = None,
) -> object:
    """Build a namespace-like object accepted by run_notion_import."""

    class Args:
        pass

    args = Args()
    args.enex_source = Path(enex_source).expanduser().resolve()
    args.processing_directory = Path(processing_dir).expanduser().resolve()
    args.notion_key = notion_key
    args.notion_root = notion_root
    args.resume = resume
    args.reset_run = reset_run
    args.wipe_local = wipe_local
    args.wipe_remote = wipe_remote
    return args


def _collect_run_cards(processing_directory: Path) -> list[RunCard]:
    """Return dashboard cards for each processing child with durable state."""
    if not processing_directory.exists() or not processing_directory.is_dir():
        return []

    cards: list[RunCard] = []
    for child in sorted(path for path in processing_directory.iterdir() if path.is_dir()):
        state_path = child / "state.db"
        if not state_path.exists():
            continue

        store = ProcessingStateStore(state_path)
        try:
            latest_run = store.latest_run_id()
            note_count = 0
            extracted_count = 0
            extraction_error_count = 0
            committed_count = 0
            pending_count = 0
            failed_count = 0
            if latest_run is not None:
                notes = store.list_notes(latest_run)
                counts = store.count_operations_by_status(latest_run)
                note_count = len(notes)
                extracted_count = sum(1 for note in notes if note.status == "extracted")
                extraction_error_count = sum(1 for note in notes if note.status == "extraction_error")
                committed_count = counts.get("committed", 0)
                pending_count = counts.get("pending", 0)
                failed_count = counts.get("failed", 0)

            cards.append(
                RunCard(
                    source_name=child.name,
                    output_directory=str(child),
                    state_path=str(state_path),
                    latest_run_id=latest_run,
                    note_count=note_count,
                    extracted_count=extracted_count,
                    extraction_error_count=extraction_error_count,
                    committed_count=committed_count,
                    pending_count=pending_count,
                    failed_count=failed_count,
                )
            )
        finally:
            store.close()

    return cards


def _redirect_with_message(processing_dir: str, message: str) -> RedirectResponse:
    target = "/?" + urlencode({"processing_dir": processing_dir, "message": message})
    return RedirectResponse(url=target, status_code=303)


def _redirect_with_error(processing_dir: str, error: str) -> RedirectResponse:
    target = "/?" + urlencode({"processing_dir": processing_dir, "error": error})
    return RedirectResponse(url=target, status_code=303)
