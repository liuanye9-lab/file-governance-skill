import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


class GovernanceDB:
    def __init__(self, db_path: str):
        db_path = Path(db_path).expanduser().resolve()
        os.makedirs(db_path.parent, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_ext TEXT,
                file_type TEXT,
                file_size INTEGER,
                file_hash TEXT NOT NULL,
                source TEXT DEFAULT 'unknown',
                source_session TEXT,
                captured_at TEXT NOT NULL,
                created_at TEXT,
                modified_at TEXT,
                author TEXT,
                page_count INTEGER,
                status TEXT DEFAULT 'pending',
                domain TEXT,
                doc_type TEXT,
                category TEXT,
                sub_category TEXT,
                tags TEXT,
                summary TEXT,
                text_content TEXT,
                drive_url TEXT,
                doc_url TEXT,
                doc_token TEXT,
                knowledge_page_status TEXT DEFAULT 'not_created',
                readback_verified INTEGER DEFAULT 0,
                target_space_id TEXT,
                target_node_token TEXT,
                target_page_path TEXT,
                publication_action TEXT DEFAULT 'create',
                permission_status TEXT DEFAULT 'unchecked',
                permission_issues TEXT,
                source_revision TEXT,
                version INTEGER DEFAULT 1,
                parent_archive TEXT,
                security_level TEXT DEFAULT 'L2-Internal',
                share_permission TEXT DEFAULT 'tenant_readable',
                collaboration_status TEXT DEFAULT '待审核',
                human_tags TEXT,
                review_note TEXT,
                review_conclusion TEXT,
                sensitivity_level TEXT DEFAULT 'none',
                sensitivity_findings TEXT,
                conflict_status TEXT,
                conflict_details TEXT,
                quality_score INTEGER DEFAULT 0,
                quality_dimensions TEXT,
                production_ready INTEGER DEFAULT 0,
                governance_action TEXT DEFAULT 'publish',
                review_priority TEXT,
                review_cycle_days INTEGER DEFAULT 0,
                next_review_at TEXT,
                review_owner TEXT,
                review_task_guid TEXT,
                review_task_url TEXT,
                review_reminder_at TEXT,
                media_status TEXT DEFAULT 'not_applicable',
                media_evidence TEXT,
                acceptance_status TEXT DEFAULT 'pending',
                acceptance_details TEXT,
                error_message TEXT,
                processing_steps TEXT,
                record_id TEXT,
                agent_record_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash);
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(source_path);
            CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
            CREATE INDEX IF NOT EXISTS idx_files_name ON files(file_name);

            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT NOT NULL,
                file_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                drive_url TEXT,
                created_at TEXT NOT NULL,
                replaced_by TEXT,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_versions_hash ON versions(file_hash);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                file_id TEXT,
                file_name TEXT,
                detail TEXT,
                success INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
        """)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """对已存在的旧库补齐后续版本新增列。"""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(files)").fetchall()}
        migrations = {
            "domain": "TEXT",
            "doc_type": "TEXT",
            "sensitivity_level": "TEXT DEFAULT 'none'",
            "sensitivity_findings": "TEXT",
            "conflict_status": "TEXT",
            "conflict_details": "TEXT",
            "quality_score": "INTEGER DEFAULT 0",
            "quality_dimensions": "TEXT",
            "production_ready": "INTEGER DEFAULT 0",
            "governance_action": "TEXT DEFAULT 'publish'",
            "review_priority": "TEXT",
            "review_cycle_days": "INTEGER DEFAULT 0",
            "next_review_at": "TEXT",
            "doc_token": "TEXT",
            "knowledge_page_status": "TEXT DEFAULT 'not_created'",
            "readback_verified": "INTEGER DEFAULT 0",
            "target_space_id": "TEXT",
            "target_node_token": "TEXT",
            "target_page_path": "TEXT",
            "publication_action": "TEXT DEFAULT 'create'",
            "permission_status": "TEXT DEFAULT 'unchecked'",
            "permission_issues": "TEXT",
            "source_revision": "TEXT",
            "review_owner": "TEXT",
            "review_task_guid": "TEXT",
            "review_task_url": "TEXT",
            "review_reminder_at": "TEXT",
            "media_status": "TEXT DEFAULT 'not_applicable'",
            "media_evidence": "TEXT",
            "acceptance_status": "TEXT DEFAULT 'pending'",
            "acceptance_details": "TEXT",
        }
        for col, column_type in migrations.items():
            if col not in cols:
                try:
                    self.conn.execute(f"ALTER TABLE files ADD COLUMN {col} {column_type}")
                except Exception:
                    pass

    def insert_file(self, record: dict):
        steps = json.dumps(record.get("processing_steps", []), ensure_ascii=False)
        tags = ", ".join(record.get("tags", [])) if isinstance(record.get("tags"), list) else record.get("tags", "")
        human_tags = ", ".join(record.get("human_tags", [])) if isinstance(record.get("human_tags"), list) else record.get("human_tags", "")
        sensitivity_findings = json.dumps(record.get("sensitivity_findings", []), ensure_ascii=False)
        conflict_details = json.dumps(record.get("conflict_details", []), ensure_ascii=False)
        quality_dimensions = json.dumps(record.get("quality_dimensions", {}), ensure_ascii=False)
        permission_issues = json.dumps(record.get("permission_issues", []), ensure_ascii=False)
        media_evidence = json.dumps(record.get("media_evidence", {}), ensure_ascii=False)
        acceptance_details = json.dumps(record.get("acceptance_details", []), ensure_ascii=False)
        self.conn.execute("""
            INSERT OR REPLACE INTO files
            (id, source_path, file_name, file_ext, file_type, file_size, file_hash,
             source, source_session, captured_at, created_at, modified_at, author, page_count,
             status, domain, doc_type, category, sub_category, tags, summary, text_content,
             drive_url, doc_url, doc_token, knowledge_page_status, readback_verified,
             target_space_id, target_node_token, target_page_path, publication_action,
             permission_status, permission_issues, source_revision,
             version, parent_archive,
             security_level, share_permission, collaboration_status,
             human_tags, review_note, review_conclusion,
             sensitivity_level, sensitivity_findings, conflict_status, conflict_details,
             quality_score, quality_dimensions, production_ready, governance_action, review_priority,
             review_cycle_days, next_review_at, review_owner, review_task_guid, review_task_url,
             review_reminder_at, media_status, media_evidence, acceptance_status, acceptance_details,
             error_message, processing_steps,
             record_id, agent_record_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record.get("id"), record.get("source_path"), record.get("file_name"),
            record.get("file_ext"), record.get("file_type"), record.get("file_size"),
            record.get("file_hash"), record.get("source"), record.get("source_session"),
            record.get("captured_at"), record.get("created_at"), record.get("modified_at"),
            record.get("author"), record.get("page_count"),
            record.get("status", "pending"), record.get("domain"), record.get("doc_type"),
            record.get("category"), record.get("sub_category"),
            tags, record.get("summary"), record.get("text_content", ""),
            record.get("drive_url", ""), record.get("doc_url", ""),
            record.get("doc_token", ""), record.get("knowledge_page_status", "not_created"),
            1 if record.get("readback_verified", False) else 0,
            record.get("target_space_id", ""), record.get("target_node_token", ""),
            record.get("target_page_path", ""), record.get("publication_action", "create"),
            record.get("permission_status", "unchecked"), permission_issues,
            record.get("source_revision", ""),
            record.get("version", 1), record.get("parent_archive"),
            record.get("security_level", "L2-Internal"),
            record.get("share_permission", "tenant_readable"),
            record.get("collaboration_status", "待审核"),
            human_tags, record.get("review_note", ""), record.get("review_conclusion", ""),
            record.get("sensitivity_level", "none"), sensitivity_findings,
            record.get("conflict_status", ""), conflict_details,
            record.get("quality_score", 0), quality_dimensions,
            1 if record.get("production_ready", False) else 0,
            record.get("governance_action", "publish"), record.get("review_priority", ""),
            record.get("review_cycle_days", 0), record.get("next_review_at"),
            record.get("review_owner", ""), record.get("review_task_guid", ""),
            record.get("review_task_url", ""), record.get("review_reminder_at"),
            record.get("media_status", "not_applicable"), media_evidence,
            record.get("acceptance_status", "pending"), acceptance_details,
            record.get("error_message", ""), steps,
            record.get("record_id", ""), record.get("agent_record_id", ""),
        ))
        self.conn.commit()

    def is_path_processed(self, source_path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM files WHERE source_path = ? "
            "AND status IN ('done','skipped','planned','pending_review')",
            (source_path,)
        ).fetchone()
        return row is not None

    def hash_exists(self, file_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM files WHERE file_hash = ? AND status = 'done'",
            (file_hash,)
        ).fetchone()
        return row is not None

    def find_by_hash(self, file_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM files WHERE file_hash = ? ORDER BY version DESC LIMIT 1",
            (file_hash,)
        ).fetchone()
        return dict(row) if row else None

    def find_done_by_hash(self, file_hash: str) -> Optional[dict]:
        """只返回已成功入库(done)的最新版本记录，用于版本去重判定。

        避免 failed/pending 记录导致版本号虚增或误判为重复。
        """
        row = self.conn.execute(
            "SELECT * FROM files WHERE file_hash = ? AND status = 'done' "
            "ORDER BY version DESC LIMIT 1",
            (file_hash,)
        ).fetchone()
        return dict(row) if row else None

    def find_by_name(self, file_name: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE file_name = ? ORDER BY version DESC",
            (file_name,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_files(self, status: str = "") -> list[dict]:
        if status:
            rows = self.conn.execute("SELECT * FROM files WHERE status = ? ORDER BY captured_at DESC", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM files ORDER BY captured_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("tags"):
                d["tags"] = [t.strip() for t in d["tags"].split(",") if t.strip()]
            else:
                d["tags"] = []
            if d.get("human_tags"):
                d["human_tags"] = [t.strip() for t in d["human_tags"].split(",") if t.strip()]
            else:
                d["human_tags"] = []
            try:
                d["processing_steps"] = json.loads(d.get("processing_steps") or "[]")
            except Exception:
                d["processing_steps"] = []
            for field, default in (
                ("sensitivity_findings", []),
                ("conflict_details", []),
                ("quality_dimensions", {}),
                ("permission_issues", []),
                ("media_evidence", {}),
                ("acceptance_details", []),
            ):
                try:
                    d[field] = json.loads(d.get(field) or json.dumps(default))
                except Exception:
                    d[field] = default
            d["production_ready"] = bool(d.get("production_ready"))
            d["readback_verified"] = bool(d.get("readback_verified"))
            result.append(d)
        return result

    def get_successful_records(self) -> list[dict]:
        return self.get_all_files(status="done")

    def get_review_queue(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE status IN ('planned', 'pending_review') "
            "ORDER BY captured_at ASC"
        ).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            for field, default in (
                ("sensitivity_findings", []),
                ("conflict_details", []),
                ("quality_dimensions", {}),
                ("permission_issues", []),
                ("media_evidence", {}),
                ("acceptance_details", []),
                ("processing_steps", []),
            ):
                try:
                    record[field] = json.loads(record.get(field) or json.dumps(default))
                except Exception:
                    record[field] = default
            record["tags"] = [t.strip() for t in (record.get("tags") or "").split(",") if t.strip()]
            record["human_tags"] = [
                t.strip() for t in (record.get("human_tags") or "").split(",") if t.strip()
            ]
            record["production_ready"] = bool(record.get("production_ready"))
            record["readback_verified"] = bool(record.get("readback_verified"))
            result.append(record)
        return result

    def get_due_reviews(self, as_of: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, file_name, domain, doc_type, category, version, "
            "quality_score, production_ready, review_priority, next_review_at, review_owner, "
            "review_task_guid, review_task_url, review_reminder_at, drive_url, doc_url "
            "FROM files WHERE status = 'done' AND COALESCE(next_review_at, '') != '' "
            "AND next_review_at <= ? ORDER BY next_review_at ASC, review_priority ASC",
            (as_of,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_review_task(
        self,
        record_id: str,
        task_guid: str,
        task_url: str,
        owner: str = "",
        reminder_at: str = "",
    ):
        self.conn.execute(
            "UPDATE files SET review_task_guid = ?, review_task_url = ?, "
            "review_owner = ?, review_reminder_at = ? WHERE id = ?",
            (task_guid, task_url, owner, reminder_at, record_id),
        )
        self.conn.commit()

    def update_knowledge_page(
        self,
        record_id: str,
        doc_token: str,
        doc_url: str,
        status: str,
        readback_verified: bool,
    ):
        self.conn.execute(
            "UPDATE files SET doc_token = ?, doc_url = ?, knowledge_page_status = ?, "
            "readback_verified = ? WHERE id = ?",
            (doc_token, doc_url, status, 1 if readback_verified else 0, record_id),
        )
        self.conn.commit()

    def add_version(self, file_hash: str, file_name: str, version: int, source_path: str, drive_url: str = "", notes: str = ""):
        self.conn.execute(
            "INSERT INTO versions (file_hash, file_name, version, source_path, drive_url, created_at, notes) VALUES (?,?,?,?,?,?,?)",
            (file_hash, file_name, version, source_path, drive_url, datetime.now().isoformat(), notes)
        )
        self.conn.commit()

    def get_versions(self, file_hash: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM versions WHERE file_hash = ? ORDER BY version DESC", (file_hash,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_version_drive_url(self, file_hash: str, version: int, drive_url: str):
        self.conn.execute(
            "UPDATE versions SET drive_url = ? WHERE file_hash = ? AND version = ?",
            (drive_url, file_hash, version),
        )
        self.conn.commit()

    def log_audit(self, action: str, file_id: str = "", file_name: str = "", detail: str = "", success: bool = True):
        self.conn.execute(
            "INSERT INTO audit_log (action, file_id, file_name, detail, success, created_at) VALUES (?,?,?,?,?,?)",
            (action, file_id, file_name, detail, 1 if success else 0, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_sync_state(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_sync_state(self, key: str, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), datetime.now().isoformat())
        )
        self.conn.commit()

    def clear_all_records(self):
        """全量刷新时清空本地记录，包括同步状态（否则 refresh 后 Agent 上下文无法重建）。"""
        self.conn.execute("DELETE FROM files")
        self.conn.execute("DELETE FROM versions")
        self.conn.execute("DELETE FROM sync_state")
        self.conn.commit()

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        done = self.conn.execute("SELECT COUNT(*) FROM files WHERE status = 'done'").fetchone()[0]
        pending_review = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status IN ('planned','pending_review')"
        ).fetchone()[0]
        skipped = self.conn.execute("SELECT COUNT(*) FROM files WHERE status = 'skipped'").fetchone()[0]
        failed = self.conn.execute("SELECT COUNT(*) FROM files WHERE status = 'failed'").fetchone()[0]
        production_ready = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status = 'done' AND production_ready = 1"
        ).fetchone()[0]
        cats = {}
        for r in self.conn.execute("SELECT category, COUNT(*) as c FROM files WHERE status = 'done' AND category IS NOT NULL GROUP BY category").fetchall():
            cats[r["category"]] = r["c"]
        types = {}
        for r in self.conn.execute("SELECT file_type, COUNT(*) as c FROM files WHERE status = 'done' AND file_type IS NOT NULL GROUP BY file_type").fetchall():
            types[r["file_type"]] = r["c"]
        domains = {}
        for r in self.conn.execute(
            "SELECT domain, COUNT(*) as c FROM files WHERE status = 'done' "
            "AND domain IS NOT NULL GROUP BY domain"
        ).fetchall():
            domains[r["domain"]] = r["c"]
        return {
            "total": total,
            "done": done,
            "pending_review": pending_review,
            "skipped": skipped,
            "failed": failed,
            "production_ready": production_ready,
            "by_category": cats,
            "by_domain": domains,
            "by_type": types,
        }

    def close(self):
        self.conn.close()
