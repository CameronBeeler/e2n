"""Models for Notion exception records and manual correction markers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ExceptionReason(StrEnum):
    """Reason values for the Notion exception database multi-select field."""

    EMPTY_TITLE = "Empty Title"
    ENCRYPTED = "Encrypted"
    EVERNOTE_LINK = "Evernote Link"
    NO_CONTENT = "No Content"
    UNSUPPORTED_CONTENT = "Unsupported Content"


@dataclass(frozen=True)
class UnsupportedAttributeGap:
    """A known Evernote attribute that cannot currently be represented through Notion APIs."""

    evernote_attribute: str
    notion_target: str
    documentation_gap: str
    future_objective: str
    manual_correction_message: str
    resource_paths: tuple[Path, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NoteExceptionRecord:
    """Exception database values associated with one imported note."""

    note_id: str
    note_title: str
    reasons: tuple[ExceptionReason | str, ...]
    source_path: Path | None = None
    block_url: str = ""
    link_text: str = ""
    link_value: str = ""

    @property
    def reason_values(self) -> tuple[str, ...]:
        """Return reason values ready for a Notion multi-select property."""
        return tuple(str(reason) for reason in self.reasons)


@dataclass(frozen=True)
class ManualCorrectionMarker:
    """Information needed to mark an imported Notion page and create an exception database row."""

    note_id: str
    note_title: str
    marker_text: str
    exception_message: str
    resource_paths: tuple[Path, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class UnsupportedContentRecord:
    """Exception values for content that could not be imported into Notion blocks."""

    note_id: str
    note_title: str
    error_comment: str
    source_path: Path | None = None
    resource_paths: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def reasons(self) -> tuple[ExceptionReason, ...]:
        """Return exception database reasons for unsupported content."""
        return (ExceptionReason.UNSUPPORTED_CONTENT,)

    @property
    def marker_text(self) -> str:
        """Return the visible Notion block text to add where conversion failed."""
        return f"Unsupported content could not be imported automatically: {self.error_comment}"


@dataclass(frozen=True)
class EvernoteEmbeddedLinkRecord:
    """Exception values for an Evernote note link that must be resolved manually."""

    note_id: str
    note_title: str
    link_text: str
    link_value: str
    source_path: Path | None = None
    block_url: str = ""

    @property
    def reasons(self) -> tuple[ExceptionReason, ...]:
        """Return exception database reasons for embedded Evernote links."""
        return (ExceptionReason.EVERNOTE_LINK,)

    @property
    def marker_text(self) -> str:
        """Return the visible Notion block text for the unresolved Evernote link."""
        return f"Evernote link requires manual resolution: {self.link_text}"


def manual_correction_marker(
    note_id: str,
    note_title: str,
    gap: UnsupportedAttributeGap,
) -> ManualCorrectionMarker:
    """Build the marker payload for an unsupported attribute gap."""
    marker_text = (
        f"Manual correction required: Evernote attribute {gap.evernote_attribute!r} "
        f"could not be mapped to {gap.notion_target}."
    )
    exception_message = (
        f"{gap.manual_correction_message} Documentation gap: {gap.documentation_gap} "
        f"Future objective: {gap.future_objective}"
    )
    return ManualCorrectionMarker(
        note_id=note_id,
        note_title=note_title,
        marker_text=marker_text,
        exception_message=exception_message,
        resource_paths=gap.resource_paths,
    )
