import unittest
from types import SimpleNamespace

from src.governance.planner import GovernancePlanner


def make_record(**overrides):
    values = {
        "id": "record-1",
        "file_name": "policy.md",
        "source_path": "/tmp/policy.md",
        "source": "test",
        "domain": "运营管理",
        "doc_type": "制度规范",
        "category": "制度",
        "sub_category": "发布",
        "version": 1,
        "is_new_version": False,
        "file_size": 128,
        "file_type": "markdown",
        "text_content": "# 发布制度\n有效正文",
        "sensitivity_level": "none",
        "sensitivity_findings": [],
        "conflict_status": "",
        "conflict_details": [],
        "quality_score": 90,
        "production_ready": True,
        "review_priority": "P2",
        "review_cycle_days": 90,
        "next_review_at": "2026-12-01",
        "governance_action": "publish",
        "review_conclusion": "生产就绪",
        "summary": "发布制度。",
        "status": "planned",
        "error_message": "",
        "security_level": "L2-Internal",
        "share_permission": "tenant_readable",
        "target_space_id": "space-1",
        "target_node_token": "node-1",
        "target_page_path": "制度/发布/policy",
        "permission_status": "verified",
        "permission_issues": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PublicationPlannerTests(unittest.TestCase):
    def test_manifest_supports_all_official_publication_actions(self):
        actions = GovernancePlanner.PUBLICATION_ACTIONS
        records = [
            make_record(
                id=f"record-{index}",
                file_name=f"{action}.md",
                publication_action=action,
            )
            for index, action in enumerate(actions)
        ]

        manifest = GovernancePlanner.build_manifest(records)

        self.assertEqual(
            [item["action"] for item in manifest["publish_plan"]],
            list(actions),
        )
        self.assertEqual(
            manifest["summary"]["actions"],
            {action: 1 for action in actions},
        )
        self.assertEqual(manifest["summary"]["statuses"]["ready"], 5)
        self.assertEqual(manifest["summary"]["statuses"]["pending"], 1)
        self.assertEqual(manifest["summary"]["statuses"]["excluded"], 1)

    def test_explicit_exclude_remains_non_publishable_for_low_quality_record(self):
        record = make_record(
            publication_action="exclude",
            production_ready=False,
            governance_action="review",
        )

        manifest = GovernancePlanner.build_manifest([record])

        plan = manifest["publish_plan"][0]
        self.assertEqual(plan["action"], "exclude")
        self.assertEqual(plan["status"], "excluded")
        self.assertFalse(plan["publishable"])

    def test_high_sensitivity_and_conflict_force_pending_blocked(self):
        high = make_record(
            id="high",
            publication_action="create",
            sensitivity_level="high",
        )
        conflict = make_record(
            id="conflict",
            publication_action="update",
            conflict_status="same_name_different_content",
        )

        manifest = GovernancePlanner.build_manifest([high, conflict])

        for item in manifest["publish_plan"]:
            self.assertEqual(item["action"], "pending")
            self.assertEqual(item["status"], "blocked")
            self.assertFalse(item["publishable"])
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["summary"]["publishable"], 0)

    def test_new_version_without_future_action_is_inferred_as_update(self):
        legacy = make_record(version=2, is_new_version=True).__dict__
        for field_name in (
            "publication_action",
            "target_space_id",
            "target_node_token",
            "target_page_path",
            "permission_status",
            "permission_issues",
        ):
            legacy.pop(field_name, None)

        manifest = GovernancePlanner.build_manifest([SimpleNamespace(**legacy)])

        plan = manifest["publish_plan"][0]
        self.assertEqual(plan["action"], "update")
        self.assertEqual(plan["status"], "pending")
        self.assertFalse(plan["publishable"])
        self.assertEqual(manifest["directory_mapping"][0]["status"], "unmapped")
        issue_codes = {
            issue["code"]
            for issue in manifest["permission_issues"][0]["issues"]
        }
        self.assertIn("missing_target_space_id", issue_codes)
        self.assertIn("missing_target_node_token", issue_codes)
        self.assertIn("permission_not_verified", issue_codes)

    def test_directory_mapping_and_permission_preflight_are_explicit(self):
        ready = make_record(publication_action="update")
        incomplete = make_record(
            id="incomplete",
            file_name="new.md",
            publication_action="create",
            target_space_id="",
            target_node_token="",
            target_page_path="制度/发布/new",
            permission_status="unchecked",
        )

        manifest = GovernancePlanner.build_manifest([ready, incomplete])

        mappings = {item["id"]: item for item in manifest["directory_mapping"]}
        self.assertEqual(mappings["record-1"]["status"], "mapped")
        self.assertEqual(mappings["incomplete"]["status"], "partial")

        plans = {item["id"]: item for item in manifest["publish_plan"]}
        self.assertEqual(plans["record-1"]["status"], "ready")
        self.assertTrue(plans["record-1"]["publishable"])
        self.assertEqual(plans["incomplete"]["status"], "pending")
        self.assertFalse(plans["incomplete"]["publishable"])

        issues = manifest["permission_issues"][0]
        self.assertEqual(issues["id"], "incomplete")
        self.assertEqual(issues["status"], "pending")
        self.assertEqual(
            {item["code"] for item in issues["issues"]},
            {"missing_target_space_id", "permission_not_verified"},
        )

    def test_permission_policy_mismatch_blocks_otherwise_ready_action(self):
        record = make_record(
            publication_action="reference",
            security_level="L3-Confidential",
            share_permission="private",
        )

        manifest = GovernancePlanner.build_manifest([record])

        plan = manifest["publish_plan"][0]
        self.assertEqual(plan["action"], "reference")
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["publishable"])
        issue_codes = {
            issue["code"]
            for issue in manifest["permission_issues"][0]["issues"]
        }
        self.assertEqual(
            issue_codes,
            {"security_level_mismatch", "share_permission_mismatch"},
        )

    def test_readable_permission_still_waits_for_write_verification(self):
        record = make_record(
            publication_action="create",
            permission_status="readable",
            permission_issues=[{
                "code": "write_unverified",
                "message": "目标可读；写权限需在实际发布时验证",
            }],
        )

        manifest = GovernancePlanner.build_manifest([record])

        plan = manifest["publish_plan"][0]
        self.assertEqual(plan["status"], "pending")
        self.assertFalse(plan["publishable"])
        issue = manifest["permission_issues"][0]
        self.assertEqual(issue["status"], "pending")
        self.assertEqual(issue["issues"][0]["code"], "write_unverified")


if __name__ == "__main__":
    unittest.main()
