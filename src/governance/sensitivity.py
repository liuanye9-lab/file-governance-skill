import re

from ..models.file_record import FileRecord


class SensitivityScanner:
    """规则化敏感信息初筛。

    只保存脱敏证据，不把完整身份证、银行卡、密钥等写入日志或上下文。
    该检查用于发布前风险分流，不替代企业 DLP 或人工合规审核。
    """

    PATTERNS = (
        ("credential", "high", re.compile(
            r"(?i)(?:api[_ -]?key|access[_ -]?token|secret|password|passwd|私钥|密码)"
            r"\s*[:=：]\s*['\"]?([A-Za-z0-9_./+=-]{8,})"
        )),
        ("private_key", "high", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("identity_card", "high", re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")),
        ("bank_card", "high", re.compile(r"(?<!\d)(\d{16,19})(?!\d)")),
        ("mobile", "medium", re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")),
        ("email", "medium", re.compile(
            r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
        )),
    )

    LEVEL_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

    def process(self, record: FileRecord) -> FileRecord:
        text = record.text_content or ""
        findings = []
        highest = "none"
        for kind, level, pattern in self.PATTERNS:
            matches = list(pattern.finditer(text))[:3]
            if not matches:
                continue
            highest = max(highest, level, key=self.LEVEL_RANK.get)
            findings.append({
                "type": kind,
                "level": level,
                "count": len(matches),
                "evidence": [self._mask(m.group(1) if m.groups() else m.group(0)) for m in matches],
            })

        record.sensitivity_level = highest
        record.sensitivity_findings = findings
        if findings:
            record.text_content = self.redact(record.text_content)
            if record.summary:
                record.summary = self.redact(record.summary)
        if highest == "high":
            record.governance_action = "hold"
            record.review_priority = "P0"
        elif highest == "medium" and not record.review_priority:
            record.review_priority = "P1"
        record.log_step(
            "sensitivity",
            f"level={highest}, findings={sum(item['count'] for item in findings)}",
        )
        return record

    def redact(self, text: str) -> str:
        redacted = text or ""
        for kind, _level, pattern in self.PATTERNS:
            redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
        return redacted

    @staticmethod
    def _mask(value: str) -> str:
        value = str(value)
        if len(value) <= 4:
            return "*" * len(value)
        if len(value) <= 8:
            return f"{value[:1]}***{value[-1:]}"
        return f"{value[:3]}***{value[-3:]}"
