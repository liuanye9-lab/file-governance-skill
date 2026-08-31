"""Unified acceptance gate for published knowledge artifacts."""

from dataclasses import dataclass, field
import re
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import urlparse


SUCCESS_VALUES = {"success", "succeeded", "ok", "passed", "verified", "referenced", "complete", "completed", "done", "已完成", "成功"}
FAILED_VALUES = {"failed", "failure", "error", "invalid", "失败"}
BLOCKED_VALUES = {"blocked", "denied", "rejected", "拦截", "阻断"}
PENDING_VALUES = {"pending", "waiting", "unknown", "not_started", "processing", "待处理", "待回读"}


@dataclass
class AcceptanceResult(Mapping[str, Any]):
    status: str
    checks: dict[str, dict[str, Any]]
    reasons: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "success"

    @property
    def passed(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "passed": self.passed,
            "checks": self.checks,
            "reasons": list(self.reasons),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class PublicationAcceptance:
    """Validate all evidence required before a publication is accepted.

    The accepted input can be a FileRecord-like object, a dictionary, or a
    record plus an overriding publication/readback dictionary.
    """

    def __init__(
        self,
        expected_security_level: str = "L2-Internal",
        expected_share_permission: str = "tenant_readable",
    ):
        self.expected_security_level = expected_security_level
        self.expected_share_permission = expected_share_permission

    def evaluate(
        self,
        record: Any = None,
        publication: Optional[dict] = None,
        **evidence: Any,
    ) -> AcceptanceResult:
        data = self._as_dict(record)
        if publication:
            data.update(publication)
        data.update(evidence)

        checks = {
            "knowledge_page_link": self._check_knowledge_page(data),
            "original_file_source": self._check_original_source(data),
            "governance_table": self._check_governance_table(data),
            "body": self._check_body(data),
            "production_ready": self._check_production_ready(data),
            "permissions": self._check_permissions(data),
            "readback": self._check_readback(data),
        }
        statuses = {check["status"] for check in checks.values()}
        # Governance and permission violations prevent release even when other
        # publication evidence is absent. A concrete technical failure outranks
        # a pending asynchronous verification.
        if "blocked" in statuses:
            status = "blocked"
        elif "failed" in statuses:
            status = "failed"
        elif "pending" in statuses:
            status = "pending"
        else:
            status = "success"
        reasons = [
            f"{name}: {check['detail']}"
            for name, check in checks.items()
            if check["status"] != "success"
        ]
        result = AcceptanceResult(status=status, checks=checks, reasons=reasons)
        self._store_result(record, result)
        return result

    check = evaluate
    validate = evaluate
    accept = evaluate
    process = evaluate

    @staticmethod
    def _check_knowledge_page(data: dict) -> dict:
        value = _first(
            data,
            "knowledge_page_url",
            "knowledge_page_link",
            "knowledge_url",
            "doc_url",
            "page_url",
        )
        if not value:
            value = _nested_first(data, ("knowledge_page", "knowledge"), ("url", "link"))
        if not value:
            return _check("failed", "Knowledge page link is missing.")
        if not _is_http_url(value):
            return _check("failed", "Knowledge page link is not a valid HTTP(S) URL.", value)
        page_status = _normalise_status(_first(data, "knowledge_page_status"))
        raw_page_status = _first(data, "knowledge_page_status")
        if page_status in {"failed", "blocked"}:
            return _check("failed", "Knowledge page publication failed.", raw_page_status)
        if raw_page_status and page_status == "pending":
            return _check("pending", "Knowledge page publication is pending.", raw_page_status)
        return _check("success", "Knowledge page link is present.", value)

    @staticmethod
    def _check_original_source(data: dict) -> dict:
        value = _first(
            data,
            "original_file_url",
            "original_file_source",
            "source_file_url",
            "original_source",
            "source_url",
            "drive_url",
            "source_path",
            "source_revision",
        )
        if not value:
            value = _nested_first(
                data,
                ("original_file", "source_file", "source"),
                ("url", "link", "path", "reference"),
            )
        if not value:
            return _check("failed", "Original file source is missing.")
        value = str(value).strip()
        if _looks_like_url(value) and not _is_http_url(value):
            return _check("failed", "Original file source URL is invalid.", value)
        return _check("success", "Original file source is traceable.", value)

    @staticmethod
    def _check_governance_table(data: dict) -> dict:
        value = _first(
            data,
            "governance_record_id",
            "governance_table_record",
            "knowledge_record_id",
            "table_record_id",
            "record_id",
            "governance_table_url",
        )
        if not value:
            nested = data.get("governance_table")
            if isinstance(nested, dict):
                value = _first(nested, "record_id", "id", "url", "link")
            elif nested is True:
                value = True
            elif nested:
                value = nested
        if not value:
            return _check("failed", "Governance table record is missing.")
        nested = data.get("governance_table")
        if isinstance(nested, dict):
            verified = _as_bool(
                _first(nested, "verified", "readback_verified")
            )
            if verified is False:
                return _check(
                    "failed",
                    "Governance table readback failed.",
                    value,
                )
            if verified is not True:
                return _check(
                    "pending",
                    "Governance table readback is pending.",
                    value,
                )
        return _check("success", "Governance table record is present.", value)

    @staticmethod
    def _check_body(data: dict) -> dict:
        value = _first(data, "body", "content", "text_content", "knowledge_body")
        if isinstance(value, dict):
            value = _first(value, "text", "content", "body")
        text = str(value or "").strip()
        media_status = str(_first(data, "media_status") or "").lower()
        media_resolution = data.get("media_resolution") or data.get("media_evidence")
        if isinstance(media_resolution, dict):
            media_status = str(media_resolution.get("status") or media_status).lower()
        if media_status == "unresolved":
            return _check("blocked", "Media content is unresolved.")
        if not text:
            return _check("failed", "Knowledge body is empty.")
        if _is_placeholder_body(text):
            return _check("failed", "Knowledge body contains only a media placeholder.")
        return _check("success", "Knowledge body is present.", f"{len(text)} characters")

    @staticmethod
    def _check_production_ready(data: dict) -> dict:
        if "production_ready" not in data:
            return _check("pending", "production_ready has not been decided.")
        value = _as_bool(data.get("production_ready"))
        if value is True:
            return _check("success", "production_ready is true.", True)
        if value is False:
            return _check("blocked", "production_ready is false.", False)
        return _check("pending", "production_ready is not a recognised boolean.", data.get("production_ready"))

    def _check_permissions(self, data: dict) -> dict:
        nested = data.get("permissions") or data.get("permission")
        nested_is_evidence = isinstance(nested, dict)
        permission = nested if nested_is_evidence else {}
        security_level = (
            _first(permission, "security_level", "level", "classification")
            or _first(data, "security_level")
        )
        share_permission = (
            _first(permission, "share_permission", "share", "visibility")
            or _first(data, "share_permission")
        )
        raw_status = (
            _first(permission, "status", "verification_status")
            or _first(data, "permission_status")
        )
        status = _normalise_status(raw_status)
        verified = (
            nested_is_evidence
            or _as_bool(_first(data, "permission_verified")) is True
            or self._has_successful_permission_step(data)
        )

        if status in {"failed", "blocked"}:
            return _check("blocked", "Permission application or verification failed.", raw_status)
        blocking_issues = [
            issue for issue in data.get("permission_issues", []) or []
            if isinstance(issue, dict)
            and str(issue.get("severity") or "").lower() in {"blocker", "error"}
        ]
        if blocking_issues:
            return _check("blocked", "Permission checks contain blocking issues.", blocking_issues)
        mismatches = []
        if security_level != self.expected_security_level:
            mismatches.append(
                f"security_level={security_level or 'missing'} "
                f"(expected {self.expected_security_level})"
            )
        if share_permission != self.expected_share_permission:
            mismatches.append(
                f"share_permission={share_permission or 'missing'} "
                f"(expected {self.expected_share_permission})"
            )
        if mismatches:
            return _check("blocked", "Permission policy mismatch: " + "; ".join(mismatches))
        if status == "pending":
            return _check("pending", "Permission verification is pending.")
        if not verified and status != "success":
            return _check("pending", "Permission values exist but have not been verified.")
        return _check(
            "success",
            "Permission policy is applied and verified.",
            {
                "security_level": security_level,
                "share_permission": share_permission,
            },
        )

    @staticmethod
    def _check_readback(data: dict) -> dict:
        readback = data.get("readback")
        raw_status = _first(data, "readback_status")
        details = readback
        if isinstance(readback, dict):
            raw_status = _first(readback, "status", "state", "result") or raw_status
        elif isinstance(readback, bool):
            raw_status = "success" if readback else "failed"
        elif isinstance(readback, str):
            raw_status = readback
        if _as_bool(_first(data, "readback_verified")) is True:
            raw_status = "success"

        status = _normalise_status(raw_status)
        if status == "success":
            return _check("success", "Published data was read back successfully.", details or raw_status)
        if status in {"failed", "blocked"}:
            return _check("failed", "Published data readback failed.", details or raw_status)
        return _check("pending", "Published data readback is pending.", details or raw_status)

    @staticmethod
    def _has_successful_permission_step(data: dict) -> bool:
        steps = data.get("processing_steps") or []
        for step in steps:
            if not isinstance(step, dict) or step.get("step") != "permission":
                continue
            if step.get("success") is not True:
                continue
            detail = str(step.get("detail") or "")
            if "tenant_readable" in detail or "共享=tenant_readable" in detail:
                return True
        return False

    @staticmethod
    def _store_result(record: Any, result: AcceptanceResult) -> None:
        if record is None:
            return
        details = [
            {
                "check": name,
                "status": check["status"],
                "passed": check["status"] == "success",
                "detail": check["detail"],
                **({"value": check["value"]} if "value" in check else {}),
            }
            for name, check in result.checks.items()
        ]
        if isinstance(record, dict):
            record["acceptance_status"] = result.status
            record["acceptance_details"] = details
            return
        try:
            setattr(record, "acceptance_status", result.status)
            setattr(record, "acceptance_details", details)
            log_step = getattr(record, "log_step", None)
            if callable(log_step):
                log_step(
                    "acceptance",
                    f"status={result.status}; checks={len(details)}",
                    success=result.status == "success",
                )
        except (AttributeError, TypeError):
            return

    @staticmethod
    def _as_dict(value: Any) -> dict:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        try:
            return dict(vars(value))
        except TypeError:
            raise TypeError("Acceptance input must be a mapping or record-like object.")


class AcceptanceChecker(PublicationAcceptance):
    pass


class AcceptanceValidator(PublicationAcceptance):
    pass


class PublicationAcceptanceChecker(PublicationAcceptance):
    pass


class UnifiedPublicationAcceptance(PublicationAcceptance):
    pass


def evaluate_acceptance(
    record: Any = None,
    publication: Optional[dict] = None,
    **evidence: Any,
) -> AcceptanceResult:
    return PublicationAcceptance().evaluate(record, publication, **evidence)


def _check(status: str, detail: str, value: Any = None) -> dict[str, Any]:
    result = {"status": status, "detail": detail}
    if value is not None:
        result["value"] = value
    return result


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _nested_first(data: dict, containers: tuple[str, ...], keys: tuple[str, ...]) -> Any:
    for container in containers:
        nested = data.get(container)
        value = _first(nested, *keys)
        if value:
            return value
    return None


def _normalise_status(value: Any) -> str:
    if value is True:
        return "success"
    if value is False:
        return "failed"
    normalised = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalised in SUCCESS_VALUES:
        return "success"
    if normalised in FAILED_VALUES:
        return "failed"
    if normalised in BLOCKED_VALUES:
        return "blocked"
    return "pending"


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalised = str(value or "").strip().lower()
    if normalised in {"true", "yes", "1", "ready", "是"}:
        return True
    if normalised in {"false", "no", "0", "not_ready", "否"}:
        return False
    return None


def _looks_like_url(value: str) -> bool:
    return "://" in value


def _is_http_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value).strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_placeholder_body(text: str) -> bool:
    stripped = text.strip()
    patterns = (
        r"^\[图片文件\].*$",
        r"^\[音频文件\].*$",
        r"^\[视频文件\].*$",
        r"^\[(?:image|audio|video)(?: file)?\].*$",
    )
    return any(re.match(pattern, stripped, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
