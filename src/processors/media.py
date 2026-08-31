"""Evidence-based media content extraction.

This module deliberately does not pretend that OCR or ASR exists. Media is
resolved only when text comes from a sidecar, injected configuration, or an
explicit callable provider.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping, Optional

from ..models.file_record import FileRecord


MEDIA_EXTENSIONS = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff"},
    "audio": {"mp3", "wav", "m4a", "aac", "flac", "ogg"},
    "video": {"mp4", "mov", "mkv", "avi", "webm", "m4v"},
}

_TIMECODE_TOKEN = r"(?:\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_TIMECODE_RANGE_RE = re.compile(
    rf"^\s*(?P<start>{_TIMECODE_TOKEN})\s*-->\s*"
    rf"(?P<end>{_TIMECODE_TOKEN})(?:\s+.*)?$"
)
_INLINE_TIMECODE_RE = re.compile(
    rf"^\s*\[?(?P<start>{_TIMECODE_TOKEN})\]?"
    r"\s*(?:[-:|]\s*)?(?P<text>.*)$"
)


@dataclass
class MediaProcessingResult(Mapping[str, Any]):
    """Structured result that supports attribute and dictionary access."""

    status: str
    media_type: str
    text_content: str = ""
    content_source: str = ""
    source_reference: str = ""
    content_kind: str = ""
    timecodes: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def content(self) -> str:
        return self.text_content

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "media_type": self.media_type,
            "text_content": self.text_content,
            "content": self.text_content,
            "content_source": self.content_source,
            "source_reference": self.source_reference,
            "content_kind": self.content_kind,
            "source": self.content_source,
            "reference": self.source_reference,
            "kind": self.content_kind,
            "timecodes": list(self.timecodes),
            "reason": self.reason,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class MediaProcessor:
    """Resolve media text from attributable sources and report degradation."""

    def __init__(self, config: Optional[dict] = None, max_text_length: int = 8000):
        self.config = self._normalise_config(config or {})
        configured_limit = self.config.get("max_text_length", max_text_length)
        self.max_text_length = max(1, int(configured_limit))

    def process(self, record: FileRecord) -> MediaProcessingResult:
        media_type = self._media_type(record)
        if not media_type:
            result = MediaProcessingResult(
                status="not_applicable",
                media_type=record.file_type or "other",
                reason="File is not a supported image, audio, or video.",
            )
            self._attach_result(record, result)
            return result

        injected = self._find_injected_content(record, media_type)
        if injected:
            text, kind, reference = injected
            return self._resolve(
                record,
                media_type,
                text,
                content_source="config",
                source_reference=reference,
                content_kind=kind,
            )

        sidecar = self._find_sidecar(record)
        if sidecar:
            path, text = sidecar
            kind = "caption" if media_type == "image" else "transcription"
            return self._resolve(
                record,
                media_type,
                text,
                content_source="sidecar",
                source_reference=str(path.resolve()),
                content_kind=kind,
            )

        capability = "ocr" if media_type == "image" else "asr"
        provider = self._provider(capability)
        if provider:
            try:
                provided = self._call_provider(provider, record)
                text, reference = self._provider_content(provided, capability)
                if text.strip():
                    kind = "caption" if media_type == "image" else "transcription"
                    return self._resolve(
                        record,
                        media_type,
                        text,
                        content_source=capability,
                        source_reference=reference,
                        content_kind=kind,
                    )
                reason = f"Configured {capability.upper()} provider returned no text."
            except Exception as exc:
                reason = f"Configured {capability.upper()} provider failed: {exc}"
        elif self._capability_configured(capability):
            reason = (
                f"{capability.upper()} is configured but no callable provider "
                "is available."
            )
        else:
            reason = (
                f"{capability.upper()} is not configured; no sidecar or injected "
                "text was found."
            )

        result = MediaProcessingResult(
            status="unresolved",
            media_type=media_type,
            reason=reason,
        )
        # Remove metadata-only placeholders so downstream quality checks cannot
        # mistake them for extracted media content.
        record.text_content = ""
        record.production_ready = False
        record.governance_action = "hold"
        record.review_priority = "P0"
        record.review_conclusion = f"媒体内容未解析：{reason}"
        record.log_step("media", f"unresolved: {reason}", success=False)
        self._attach_result(record, result)
        return result

    resolve = process

    def process_record(self, record: FileRecord) -> FileRecord:
        """Compatibility helper for processor chains that return FileRecord."""
        self.process(record)
        return record

    def _resolve(
        self,
        record: FileRecord,
        media_type: str,
        text: str,
        content_source: str,
        source_reference: str,
        content_kind: str,
    ) -> MediaProcessingResult:
        body = text.strip()
        traceable = self._traceable_content(
            record,
            body,
            content_source,
            source_reference,
            content_kind,
        )
        traceable = traceable[: self.max_text_length]
        timecodes = parse_timecodes(body) if media_type == "video" else []
        result = MediaProcessingResult(
            status="resolved",
            media_type=media_type,
            text_content=traceable,
            content_source=content_source,
            source_reference=source_reference,
            content_kind=content_kind,
            timecodes=timecodes,
        )
        record.text_content = traceable
        record.log_step(
            "media",
            f"resolved via {content_source}: {source_reference}",
        )
        self._attach_result(record, result)
        return result

    @staticmethod
    def _traceable_content(
        record: FileRecord,
        text: str,
        content_source: str,
        source_reference: str,
        content_kind: str,
    ) -> str:
        return "\n".join([
            "[MEDIA CONTENT]",
            f"original_media: {record.file_name}",
            f"content_source: {content_source}",
            f"source_reference: {MediaProcessor._public_reference(source_reference)}",
            f"content_kind: {content_kind}",
            "",
            text,
        ])

    @staticmethod
    def _public_reference(value: str) -> str:
        reference = str(value or "")
        if not reference:
            return "not_recorded"
        path = Path(reference).expanduser()
        if path.is_absolute():
            return path.name
        return reference

    @staticmethod
    def _attach_result(record: FileRecord, result: MediaProcessingResult) -> None:
        evidence = result.to_dict()
        record.media_status = result.status
        record.media_evidence = evidence
        record.media_content_source = result.content_source
        record.media_source_reference = result.source_reference
        record.media_timecodes = result.timecodes
        record.media_resolution = evidence

    @staticmethod
    def _media_type(record: FileRecord) -> str:
        if record.file_type in MEDIA_EXTENSIONS:
            return record.file_type
        ext = (record.file_ext or Path(record.source_path).suffix.lstrip(".")).lower()
        for media_type, extensions in MEDIA_EXTENSIONS.items():
            if ext in extensions:
                return media_type
        return ""

    def _find_sidecar(self, record: FileRecord) -> Optional[tuple[Path, str]]:
        sidecar_enabled = self.config.get(
            "sidecar_enabled",
            self.config.get("media_sidecar_enabled", True),
        )
        if not _as_enabled(sidecar_enabled):
            return None
        media_path = Path(record.source_path)
        configured = self.config.get("sidecar_extensions", [".txt", ".md"])
        extensions = []
        for extension in configured:
            extension = str(extension)
            extensions.append(extension if extension.startswith(".") else f".{extension}")

        candidates = []
        for extension in extensions:
            candidates.extend([
                media_path.with_suffix(media_path.suffix + extension),
                media_path.with_suffix(extension),
            ])
        seen = set()
        for candidate in candidates:
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            text = self._read_text(candidate)
            if text.strip():
                return candidate, text
        return None

    def _find_injected_content(
        self,
        record: FileRecord,
        media_type: str,
    ) -> Optional[tuple[str, str, str]]:
        identifiers = self._identifiers(record)
        file_map = self.config.get("files")
        if isinstance(file_map, dict):
            selected = self._lookup(file_map, identifiers)
            parsed = self._parse_injected_value(selected, media_type)
            if parsed:
                text, kind, reference = parsed
                return text, kind, reference or "config:files"

        keys = (
            ("caption", "captions", "transcription", "transcriptions")
            if media_type == "image"
            else ("transcription", "transcriptions", "caption", "captions")
        )
        for key in keys:
            if key not in self.config:
                continue
            value = self.config[key]
            if isinstance(value, dict) and not self._is_content_payload(value):
                selected = self._lookup(value, identifiers)
            else:
                selected = value
            parsed = self._parse_injected_value(selected, media_type, key.rstrip("s"))
            if parsed:
                text, kind, reference = parsed
                return text, kind, reference or f"config:{key}"
        return None

    @staticmethod
    def _is_content_payload(value: dict) -> bool:
        return any(
            key in value
            for key in ("text", "content", "transcription", "caption")
        )

    @staticmethod
    def _parse_injected_value(
        value: Any,
        media_type: str,
        default_kind: str = "",
    ) -> Optional[tuple[str, str, str]]:
        if isinstance(value, str) and value.strip():
            kind = default_kind or (
                "caption" if media_type == "image" else "transcription"
            )
            return value, kind, ""
        if not isinstance(value, dict):
            return None
        kind = str(value.get("kind") or default_kind or (
            "caption" if media_type == "image" else "transcription"
        ))
        text = (
            value.get("text")
            or value.get("content")
            or value.get("transcription")
            or value.get("caption")
            or ""
        )
        if not isinstance(text, str) or not text.strip():
            return None
        reference = str(
            value.get("source_reference")
            or value.get("reference")
            or value.get("source")
            or ""
        )
        return text, kind, reference

    @staticmethod
    def _identifiers(record: FileRecord) -> list[str]:
        path = Path(record.source_path)
        values = [
            record.source_path,
            str(path),
            str(path.resolve()) if path.exists() else "",
            record.file_name,
            path.name,
            path.stem,
            record.id,
            "default",
        ]
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _lookup(mapping: dict, identifiers: list[str]) -> Any:
        for identifier in identifiers:
            if identifier in mapping:
                return mapping[identifier]
        return None

    def _provider(self, capability: str) -> Optional[Callable]:
        value = self.config.get(capability)
        if callable(value):
            return value
        if isinstance(value, dict):
            for key in ("provider", "handler", "callable"):
                if callable(value.get(key)):
                    return value[key]
        return None

    def _capability_configured(self, capability: str) -> bool:
        value = self.config.get(capability)
        if callable(value):
            return True
        if isinstance(value, dict):
            return bool(value.get("enabled") or value.get("provider"))
        return bool(value) or _as_enabled(
            self.config.get(f"{capability}_enabled", False)
        )

    @staticmethod
    def _call_provider(provider: Callable, record: FileRecord) -> Any:
        try:
            return provider(record)
        except TypeError as record_error:
            try:
                return provider(record.source_path)
            except TypeError:
                raise record_error

    @staticmethod
    def _provider_content(value: Any, capability: str) -> tuple[str, str]:
        if isinstance(value, str):
            return value, f"{capability}:provider"
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or ""
            reference = (
                value.get("source_reference")
                or value.get("reference")
                or f"{capability}:provider"
            )
            return str(text), str(reference)
        return "", f"{capability}:provider"

    @staticmethod
    def _read_text(path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return ""

    @staticmethod
    def _normalise_config(config: dict) -> dict:
        if not isinstance(config, dict):
            return {}
        merged = dict(config)
        processing = config.get("processing")
        if isinstance(processing, dict):
            for key in (
                "max_text_length",
                "ocr_enabled",
                "asr_enabled",
                "media_sidecar_enabled",
            ):
                if key in processing:
                    merged[key] = processing[key]
            if isinstance(processing.get("media"), dict):
                merged.update(processing["media"])
        if isinstance(config.get("media"), dict):
            merged.update(config["media"])
        return merged


def parse_timecode(value: str) -> float:
    """Convert MM:SS(.mmm) or HH:MM:SS(.mmm) into seconds."""
    normalised = value.strip().replace(",", ".")
    parts = normalised.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid timecode: {value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid timecode: {value}") from exc
    if any(number < 0 for number in numbers) or numbers[-1] >= 60:
        raise ValueError(f"Invalid timecode: {value}")
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60:
        raise ValueError(f"Invalid timecode: {value}")
    return hours * 3600 + minutes * 60 + seconds


def parse_timecodes(text: str) -> list[dict[str, Any]]:
    """Parse SRT/VTT ranges and line-leading timestamps from transcript text."""
    lines = text.splitlines()
    timecodes = []
    seen = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        range_match = _TIMECODE_RANGE_RE.match(line)
        if range_match:
            following = []
            cursor = index + 1
            while cursor < len(lines) and lines[cursor].strip():
                if not lines[cursor].strip().isdigit():
                    following.append(lines[cursor].strip())
                cursor += 1
            item = _timecode_item(
                range_match.group("start"),
                " ".join(following),
                range_match.group("end"),
            )
            key = (item["start"], item.get("end"), item["text"])
            if key not in seen:
                seen.add(key)
                timecodes.append(item)
            index = cursor
            continue

        inline_match = _INLINE_TIMECODE_RE.match(line)
        if inline_match and inline_match.group("text").strip():
            item = _timecode_item(
                inline_match.group("start"),
                inline_match.group("text").strip(),
            )
            key = (item["start"], None, item["text"])
            if key not in seen:
                seen.add(key)
                timecodes.append(item)
        index += 1
    return timecodes


def _timecode_item(start: str, text: str, end: str = "") -> dict[str, Any]:
    item = {
        "raw": start,
        "start": start,
        "seconds": parse_timecode(start),
        "start_seconds": parse_timecode(start),
        "text": text,
    }
    if end:
        item.update({
            "end": end,
            "end_seconds": parse_timecode(end),
        })
    return item


def _as_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    return bool(value)


MediaGovernanceProcessor = MediaProcessor
MediaResult = MediaProcessingResult
