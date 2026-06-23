"""ENEX extraction utilities."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from collections.abc import Iterator
from pathlib import Path
import re

from lxml import etree

from e2n.exceptions import EvernoteEmbeddedLinkRecord, ExceptionReason, NoteExceptionRecord
from e2n.state import ProcessingStateStore


EMPTY_TITLE = "Empty Title"
EVERNOTE_LINK_PATTERN = re.compile(r"^evernote:/*", re.IGNORECASE)
EVERNOTE_WEB_LINK_PATTERN = re.compile(
    r"^https?://(?:www\.)?evernote\.com/(?:shard/[^/]+/(?:nl|sh)/|l/)", re.IGNORECASE
)


@dataclass(frozen=True)
class ExtractedNote:
    """A note extracted from an ENEX file."""

    note_id: str
    title: str
    path: Path
    tags: tuple[str, ...] = ()
    exception_reasons: tuple[ExceptionReason, ...] = ()


@dataclass(frozen=True)
class ProcessingPaths:
    """Filesystem paths for one ENEX processing run."""

    output_directory: Path
    notes_directory: Path
    resources_directory: Path
    master_path: Path
    success_path: Path
    errors_path: Path
    exceptions_path: Path
    state_path: Path


@dataclass(frozen=True)
class ExtractionResult:
    """Summary of an ENEX extraction run."""

    source: Path
    output_directory: Path
    total_notes: int
    success_count: int
    error_count: int


def extract_enex_notes(enex_source: Path, processing_directory: Path) -> ExtractionResult:
    """Extract every note from an ENEX file into a named processing directory."""
    source = _validate_enex_file(enex_source)
    paths = _processing_paths(source, processing_directory)
    paths.notes_directory.mkdir(parents=True, exist_ok=True)
    paths.resources_directory.mkdir(parents=True, exist_ok=True)

    total_notes = 0
    success_count = 0
    error_count = 0
    note_exception_records: list[NoteExceptionRecord] = []
    resource_manifest: dict[str, str] = {}
    state = ProcessingStateStore(paths.state_path)
    try:
        run_id = state.begin_run(source_path=source, output_directory=paths.output_directory)

        with (
            paths.master_path.open("w", encoding="utf-8") as master_file,
            paths.success_path.open("w", encoding="utf-8") as success_file,
            paths.errors_path.open("w", encoding="utf-8") as errors_file,
        ):
            for note_number, note_element in enumerate(_iter_note_elements(source), start=1):
                total_notes += 1
                note_id = f"note_{note_number:06d}"
                note_file = paths.notes_directory / f"{note_id}.enex"
                title, title_reasons = _note_title(note_element)
                exception_reasons = title_reasons + _content_exception_reasons(note_element)
                note = ExtractedNote(
                    note_id=note_id,
                    title=title,
                    path=note_file,
                    tags=_note_tags(note_element),
                    exception_reasons=exception_reasons,
                )
                master_file.write(_note_record(note))
                try:
                    _write_note_file(note_file, note_element)
                    _extract_resources(note_element, paths.resources_directory, resource_manifest)
                    success_file.write(_note_record(note))
                    state.upsert_note(
                        run_id=run_id,
                        note=note,
                        source_path=source,
                        status="extracted",
                    )
                    if note.exception_reasons:
                        note_exception_records.append(
                            NoteExceptionRecord(
                                note_id=note.note_id,
                                note_title=note.title,
                                reasons=note.exception_reasons,
                                source_path=source,
                            )
                        )
                    for link_record in _evernote_embedded_link_records(note, note_element, source):
                        note_exception_records.append(
                            NoteExceptionRecord(
                                note_id=link_record.note_id,
                                note_title=link_record.note_title,
                                reasons=link_record.reasons,
                                source_path=link_record.source_path,
                                block_url=link_record.block_url,
                                link_text=link_record.link_text,
                                link_value=link_record.link_value,
                            )
                        )
                    success_count += 1
                except Exception as exc:
                    errors_file.write(f"{note_id}\t{title}\t{note_file}\t{exc}\n")
                    state.upsert_note(
                        run_id=run_id,
                        note=note,
                        source_path=source,
                        status="extraction_error",
                        error_message=str(exc),
                    )
                    error_count += 1

        _write_note_exception_records(paths.exceptions_path, note_exception_records)
        if resource_manifest:
            (paths.resources_directory / "manifest.json").write_text(
                json.dumps(resource_manifest, indent=2), encoding="utf-8"
            )
    finally:
        state.close()

    return ExtractionResult(
        source=source,
        output_directory=paths.output_directory,
        total_notes=total_notes,
        success_count=success_count,
        error_count=error_count,
    )


def discover_enex_sources(enex_source: Path) -> list[Path]:
    """Discover one or more ENEX source files from a file or directory path."""
    source = enex_source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"ENEX source does not exist: {source}")
    if source.is_file():
        return [_validate_enex_file(source)]
    if source.is_dir():
        sources = sorted(path for path in source.iterdir() if path.is_file() and path.suffix.lower() == ".enex")
        if not sources:
            raise FileNotFoundError(f"No .enex files found in source directory: {source}")
        return sources
    raise ValueError(f"ENEX source must be a file or directory: {source}")


def _validate_enex_file(enex_source: Path) -> Path:
    """Validate and normalize a single ENEX source file path."""
    source = enex_source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"ENEX source does not exist: {source}")
    if not source.is_file():
        raise ValueError(f"ENEX source is not a file: {source}")
    if source.suffix.lower() != ".enex":
        raise ValueError(f"ENEX source must end with .enex: {source}")
    return source


def _processing_paths(enex_source: Path, processing_directory: Path) -> ProcessingPaths:
    """Build all processing paths for a single ENEX source file."""
    output_directory = processing_directory.expanduser().resolve() / enex_source.stem
    return ProcessingPaths(
        output_directory=output_directory,
        notes_directory=output_directory / "notes",
        resources_directory=output_directory / "resources",
        master_path=output_directory / "master.txt",
        success_path=output_directory / "success.txt",
        errors_path=output_directory / "errors.txt",
        exceptions_path=output_directory / "exceptions.txt",
        state_path=output_directory / "state.db",
    )


def _iter_note_elements(source: Path) -> Iterator[etree._Element]:
    """Yield detached ENEX note elements from a source file."""
    for _event, element in etree.iterparse(str(source), events=("end",), huge_tree=True):
        if _local_name(element.tag) == "note":
            yield element
            element.clear()
            parent = element.getparent()
            if parent is not None:
                while element.getprevious() is not None:
                    del parent[0]


def _local_name(tag: str) -> str:
    """Return an XML tag's local name without a namespace."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _note_title(note_element: etree._Element) -> tuple[str, tuple[ExceptionReason, ...]]:
    """Read a note title from an ENEX note element."""
    for child in note_element:
        if _local_name(child.tag) == "title":
            title = " ".join((child.text or "").split())
            if title:
                return title, ()
            return EMPTY_TITLE, (ExceptionReason.EMPTY_TITLE,)
    return EMPTY_TITLE, (ExceptionReason.EMPTY_TITLE,)


def _note_tags(note_element: etree._Element) -> tuple[str, ...]:
    """Read Evernote tag values for the future Notion Tags multi-select property."""
    tags: list[str] = []
    for child in note_element:
        if _local_name(child.tag) == "tag":
            tag = " ".join((child.text or "").split())
            if tag and tag not in tags:
                tags.append(tag)
    return tuple(tags)


def _content_exception_reasons(note_element: etree._Element) -> tuple[ExceptionReason, ...]:
    """Return content-level exception reasons for a note."""
    if _note_has_content_or_resources(note_element):
        return ()
    return (ExceptionReason.NO_CONTENT,)


def _note_has_content_or_resources(note_element: etree._Element) -> bool:
    """Return whether a note has body content or resources, ignoring tags."""
    if any(_local_name(child.tag) == "resource" for child in note_element):
        return True

    for child in note_element:
        if _local_name(child.tag) == "content" and _content_has_body(child.text or ""):
            return True
    return False


def _content_has_body(content: str) -> bool:
    """Return whether ENML content contains meaningful body content."""
    if not content.strip():
        return False
    try:
        root = etree.fromstring(content.encode("utf-8"), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return bool(content.strip())

    if "".join(root.itertext()).strip():
        return True
    empty_markup_tags = {"en-note", "br"}
    return any(_local_name(element.tag) not in empty_markup_tags for element in root.iter())


def _evernote_embedded_link_records(
    note: ExtractedNote,
    note_element: etree._Element,
    source: Path,
) -> tuple[EvernoteEmbeddedLinkRecord, ...]:
    """Return embedded Evernote links that require post-import manual resolution."""
    records: list[EvernoteEmbeddedLinkRecord] = []
    for child in note_element:
        if _local_name(child.tag) == "content":
            for link_text, link_value in _evernote_links_from_content(child.text or ""):
                records.append(
                    EvernoteEmbeddedLinkRecord(
                        note_id=note.note_id,
                        note_title=note.title,
                        link_text=link_text,
                        link_value=link_value,
                        source_path=source,
                    )
                )
    return tuple(records)


def _evernote_links_from_content(content: str) -> tuple[tuple[str, str], ...]:
    """Return ``(link text, link value)`` pairs for Evernote links in ENML content."""
    if "evernote:" not in content.lower():
        return ()
    try:
        root = etree.fromstring(content.encode("utf-8"), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return ()

    links: list[tuple[str, str]] = []
    for element in root.iter():
        href = element.attrib.get("href", "")
        if EVERNOTE_LINK_PATTERN.match(href):
            link_text = " ".join("".join(element.itertext()).split()) or href
            links.append((link_text, href))
    return tuple(links)


def _write_note_file(path: Path, note_element: etree._Element) -> None:
    """Write a single note element as a standalone ENEX-shaped XML file."""
    note_xml = etree.tostring(note_element, encoding="utf-8")
    path.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n<en-export>\n' + note_xml + b"\n</en-export>\n")


def _extract_resources(
    note_element: etree._Element, resources_dir: Path, manifest: dict[str, str]
) -> None:
    """Decode and write resource binaries to disk, updating the manifest."""
    for child in note_element:
        if _local_name(child.tag) != "resource":
            continue
        data_el = None
        mime = ""
        file_name = ""
        for res_child in child:
            tag = _local_name(res_child.tag)
            if tag == "data":
                data_el = res_child
            elif tag == "mime":
                mime = (res_child.text or "").strip()
            elif tag == "resource-attributes":
                for attr_child in res_child:
                    if _local_name(attr_child.tag) == "file-name":
                        file_name = (attr_child.text or "").strip()

        if data_el is None or not data_el.text:
            continue

        raw = base64.b64decode(data_el.text)
        md5_hash = hashlib.md5(raw).hexdigest()

        if not file_name:
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            file_name = f"{md5_hash}.{ext}"

        dest = resources_dir / file_name
        dest.write_bytes(raw)
        manifest[md5_hash] = str(dest)


def _note_record(note: ExtractedNote) -> str:
    """Format one note tracking record."""
    tags = ",".join(note.tags)
    reasons = ",".join(str(reason) for reason in note.exception_reasons)
    return f"{note.note_id}\t{note.title}\t{note.path}\t{tags}\t{reasons}\n"


def _write_note_exception_records(path: Path, records: list[NoteExceptionRecord]) -> None:
    """Write exception database seed records for successfully extracted notes."""
    with path.open("w", encoding="utf-8") as exception_file:
        for record in records:
            exception_file.write(
                f"{record.note_id}\t{record.note_title}\t{','.join(record.reason_values)}\t{record.source_path}"
                f"\t{record.block_url}\t{record.link_text}\t{record.link_value}\n"
            )
