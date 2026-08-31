import tempfile
import unittest
from pathlib import Path

from src.governance.acceptance import PublicationAcceptance
from src.governance.quality import QualityReviewer
from src.models.file_record import FileRecord
from src.processors.media import MediaProcessor, parse_timecode, parse_timecodes


class MediaProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _record(self, name: str, content: bytes = b"media") -> FileRecord:
        path = self.root / name
        path.write_bytes(content)
        return FileRecord.from_path(str(path), "test")

    def test_media_without_ocr_or_asr_is_explicitly_unresolved(self):
        for name, capability in (
            ("poster.png", "OCR"),
            ("meeting.mp3", "ASR"),
            ("demo.mp4", "ASR"),
        ):
            with self.subTest(name=name):
                record = self._record(name)
                record.text_content = f"[{record.file_type} file] placeholder"
                result = MediaProcessor().process(record)
                self.assertEqual(result.status, "unresolved")
                self.assertIn(f"{capability} is not configured", result.reason)
                self.assertEqual(record.text_content, "")
                self.assertFalse(record.production_ready)
                self.assertEqual(record.governance_action, "hold")

    def test_sidecar_text_resolves_media_with_traceable_source(self):
        record = self._record("meeting.mp3")
        sidecar = self.root / "meeting.txt"
        sidecar.write_text("Quarterly review transcript.", encoding="utf-8")

        result = MediaProcessor().process(record)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.content_source, "sidecar")
        self.assertEqual(result.source_reference, str(sidecar.resolve()))
        self.assertIn(f"original_media: {record.file_name}", result.text_content)
        self.assertNotIn(str(self.root), result.text_content)
        self.assertIn("Quarterly review transcript.", record.text_content)

    def test_extended_audio_format_uses_sidecar_and_can_pass_quality(self):
        record = self._record("meeting.m4a")
        (self.root / "meeting.txt").write_text(
            "会议确认了预算、负责人和执行日期。",
            encoding="utf-8",
        )
        record.compute_hash()
        MediaProcessor().process(record)
        record.domain = "运营管理"
        record.doc_type = "会议纪要"
        record.category = "运营管理"
        record.tags = ["预算", "负责人"]
        record.summary = "会议形成可执行预算决议。"
        QualityReviewer().process(record)

        self.assertEqual(record.file_type, "audio")
        self.assertEqual(record.media_status, "resolved")
        self.assertTrue(record.production_ready)

    def test_injected_caption_is_scoped_and_traceable(self):
        record = self._record("poster.png")
        processor = MediaProcessor({
            "media": {
                "captions": {
                    "poster.png": {
                        "text": "Launch event poster.",
                        "source_reference": "manual-review:ticket-42",
                    },
                },
            },
        })

        result = processor.process(record)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.content_kind, "caption")
        self.assertEqual(result.source_reference, "manual-review:ticket-42")
        self.assertIn("content_source: config", result.text_content)
        self.assertEqual(record.media_evidence["kind"], "caption")
        self.assertEqual(record.media_evidence["status"], "resolved")

    def test_video_transcription_parses_inline_and_srt_timecodes(self):
        record = self._record("demo.mp4")
        transcription = (
            "1\n"
            "00:00:01,500 --> 00:00:03,000\n"
            "Introduction\n\n"
            "[00:12] Product demo\n"
        )
        result = MediaProcessor({
            "transcriptions": {"demo.mp4": transcription},
        }).process(record)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(len(result.timecodes), 2)
        self.assertEqual(result.timecodes[0]["start_seconds"], 1.5)
        self.assertEqual(result.timecodes[0]["end_seconds"], 3.0)
        self.assertEqual(result.timecodes[1]["start_seconds"], 12.0)
        self.assertEqual(parse_timecode("01:02:03.500"), 3723.5)
        self.assertEqual(len(parse_timecodes(transcription)), 2)

    def test_enabled_capability_without_callable_does_not_fake_output(self):
        record = self._record("poster.png")

        result = MediaProcessor({
            "processing": {"ocr_enabled": True},
        }).process(record)

        self.assertEqual(result.status, "unresolved")
        self.assertIn("no callable provider", result.reason)
        self.assertEqual(result.text_content, "")

    def test_direct_injected_payload_is_supported(self):
        record = self._record("meeting.mp3")
        result = MediaProcessor({
            "transcription": {
                "text": "Injected transcript.",
                "reference": "human-review:7",
            },
        }).process(record)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.source_reference, "human-review:7")


class PublicationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.acceptance = PublicationAcceptance()
        self.complete = {
            "knowledge_page_url": "https://example.feishu.cn/wiki/knowledge-token",
            "original_file_url": "https://example.feishu.cn/file/source-token",
            "governance_record_id": "rec123",
            "body": "Traceable governed knowledge content.",
            "production_ready": True,
            "permissions": {
                "security_level": "L2-Internal",
                "share_permission": "tenant_readable",
                "status": "verified",
            },
            "readback": {"status": "success", "record_id": "rec123"},
        }

    def test_complete_publication_succeeds(self):
        result = self.acceptance.evaluate(self.complete)

        self.assertEqual(result.status, "success")
        self.assertTrue(result.success)
        self.assertTrue(all(
            check["status"] == "success"
            for check in result.checks.values()
        ))
        self.assertEqual(self.complete["acceptance_status"], "success")
        self.assertEqual(len(self.complete["acceptance_details"]), 7)

    def test_not_production_ready_or_bad_permission_is_blocked(self):
        payload = dict(self.complete)
        payload["production_ready"] = False
        payload["permissions"] = {
            "security_level": "L3-Confidential",
            "share_permission": "private",
            "status": "verified",
        }

        result = self.acceptance.evaluate(payload)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.checks["production_ready"]["status"], "blocked")
        self.assertEqual(result.checks["permissions"]["status"], "blocked")

    def test_pending_readback_returns_pending(self):
        payload = dict(self.complete)
        payload["readback"] = {"status": "pending"}

        result = self.acceptance.evaluate(payload)

        self.assertEqual(result.status, "pending")
        self.assertEqual(result.checks["readback"]["status"], "pending")

    def test_reference_action_accepts_verified_source_link(self):
        payload = dict(self.complete)
        payload["knowledge_page_status"] = "referenced"
        payload["knowledge_page_url"] = (
            "https://example.feishu.cn/file/source-token"
        )

        result = self.acceptance.evaluate(payload)

        self.assertEqual(result.status, "success")

    def test_failed_readback_or_missing_publish_evidence_returns_failed(self):
        payload = dict(self.complete)
        payload["readback"] = {"status": "failed", "error": "record not found"}
        result = self.acceptance.evaluate(payload)
        self.assertEqual(result.status, "failed")

        payload = dict(self.complete)
        payload["knowledge_page_url"] = ""
        result = self.acceptance.evaluate(payload)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.checks["knowledge_page_link"]["status"], "failed")

    def test_unresolved_media_body_is_blocked(self):
        payload = dict(self.complete)
        payload["media_status"] = "unresolved"

        result = self.acceptance.evaluate(payload)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.checks["body"]["status"], "blocked")

    def test_unverified_record_defaults_remain_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.md"
            path.write_text("Policy body.", encoding="utf-8")
            record = FileRecord.from_path(str(path), "inbox")
            record.doc_url = "https://example.feishu.cn/wiki/knowledge-token"
            record.drive_url = "https://example.feishu.cn/file/source-token"
            record.record_id = "rec123"
            record.text_content = "Policy body."
            record.production_ready = True

            result = self.acceptance.evaluate(
                record,
                readback_status="success",
            )

        self.assertEqual(result.status, "pending")
        self.assertEqual(result.checks["permissions"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
