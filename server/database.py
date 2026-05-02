"""SQLite persistence layer for jobs and papers.

Shares the same database file (data/memory.db) as MemoryManager,
but manages its own tables independently.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from modules.paper_selector.fetcher import get_candidate_dedupe_key
from utils.config import DATA_DIR
from utils.logger import get_logger
from utils.repo_paths import normalize_config_paths, to_repo_relative_path

log = get_logger(__name__)

_DB_PATH = DATA_DIR / "memory.db"

_EXTRA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',
    mode            TEXT NOT NULL DEFAULT 'auto',
    profile_id      INTEGER,
    profile_mode    TEXT NOT NULL DEFAULT 'auto',
    profile_assignment_status TEXT NOT NULL DEFAULT 'pending',
    profile_assignment_note   TEXT NOT NULL DEFAULT '',
    config_snapshot  TEXT NOT NULL DEFAULT '{}',
    progress        INTEGER NOT NULL DEFAULT 0,
    current_step    TEXT NOT NULL DEFAULT '',
    error           TEXT,
    paper_title     TEXT NOT NULL DEFAULT '',
    report_path     TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL
);

CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT REFERENCES jobs(id),
    paper_id        TEXT NOT NULL,
    dedupe_key      TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    venue           TEXT NOT NULL DEFAULT '',
    pub_date        TEXT NOT NULL DEFAULT '',
    authors         TEXT NOT NULL DEFAULT '[]',
    source          TEXT NOT NULL DEFAULT '',
    match_track     TEXT NOT NULL DEFAULT '',
    selection_reason TEXT NOT NULL DEFAULT '',
    pdf_path        TEXT NOT NULL DEFAULT '',
    source_path     TEXT NOT NULL DEFAULT '',
    source_type     TEXT NOT NULL DEFAULT 'pdf',
    report_path     TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    UNIQUE(paper_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_papers_job ON papers(job_id);
"""


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._closed = False
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_EXTRA_SCHEMA_SQL)
        self._ensure_compat_schema()
        self._normalize_stored_paths()

    def _ensure_compat_schema(self) -> None:
        paper_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        if "dedupe_key" not in paper_columns:
            self._conn.execute("ALTER TABLE papers ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT ''")
        if "source_path" not in paper_columns:
            self._conn.execute("ALTER TABLE papers ADD COLUMN source_path TEXT NOT NULL DEFAULT ''")
        if "source_type" not in paper_columns:
            self._conn.execute("ALTER TABLE papers ADD COLUMN source_type TEXT NOT NULL DEFAULT 'pdf'")
        job_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "profile_mode" not in job_columns:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN profile_mode TEXT NOT NULL DEFAULT 'auto'")
        if "profile_assignment_status" not in job_columns:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN profile_assignment_status TEXT NOT NULL DEFAULT 'pending'"
            )
        if "profile_assignment_note" not in job_columns:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN profile_assignment_note TEXT NOT NULL DEFAULT ''"
            )
        self._conn.commit()

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _normalize_stored_paths(self) -> None:
        job_rows = self._conn.execute(
            "SELECT id, report_path, config_snapshot FROM jobs"
        ).fetchall()
        for row in job_rows:
            updates: dict[str, Any] = {}

            report_path = to_repo_relative_path(row["report_path"])
            if report_path != str(row["report_path"] or ""):
                updates["report_path"] = report_path

            raw_snapshot = row["config_snapshot"]
            if isinstance(raw_snapshot, str):
                try:
                    snapshot = json.loads(raw_snapshot)
                except json.JSONDecodeError:
                    snapshot = {}
            elif isinstance(raw_snapshot, dict):
                snapshot = raw_snapshot
            else:
                snapshot = {}

            normalized_snapshot = normalize_config_paths(snapshot)
            if normalized_snapshot != snapshot:
                updates["config_snapshot"] = json.dumps(
                    normalized_snapshot, ensure_ascii=False
                )

            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                values = list(updates.values()) + [row["id"]]
                self._conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)

        paper_rows = self._conn.execute(
            "SELECT paper_id, pdf_path, source_path, report_path FROM papers"
        ).fetchall()
        for row in paper_rows:
            updates: dict[str, Any] = {}
            pdf_path = to_repo_relative_path(row["pdf_path"])
            source_path = to_repo_relative_path(row["source_path"])
            report_path = to_repo_relative_path(row["report_path"])
            if pdf_path != str(row["pdf_path"] or ""):
                updates["pdf_path"] = pdf_path
            if source_path != str(row["source_path"] or ""):
                updates["source_path"] = source_path
            if report_path != str(row["report_path"] or ""):
                updates["report_path"] = report_path
            if not str(row["source_path"] or "").strip() and pdf_path:
                updates["source_path"] = pdf_path
            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                values = list(updates.values()) + [row["paper_id"]]
                self._conn.execute(
                    f"UPDATE papers SET {set_clause} WHERE paper_id = ?", values
                )

        self._conn.commit()

    def close(self) -> None:
        if self._closed:
            return
        self._conn.close()
        self._closed = True

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        try:
            self.close()
        except Exception:
            pass

    def _decode_job_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        raw_config = payload.get("config_snapshot")
        if isinstance(raw_config, str):
            try:
                payload["config_snapshot"] = normalize_config_paths(
                    json.loads(raw_config)
                )
            except json.JSONDecodeError:
                payload["config_snapshot"] = {}
        elif not isinstance(raw_config, dict):
            payload["config_snapshot"] = {}
        else:
            payload["config_snapshot"] = normalize_config_paths(raw_config)
        payload["report_path"] = to_repo_relative_path(payload.get("report_path"))
        payload["profile_mode"] = str(payload.get("profile_mode") or "auto").strip() or "auto"
        payload["profile_assignment_status"] = (
            str(payload.get("profile_assignment_status") or "pending").strip()
            or "pending"
        )
        payload["profile_assignment_note"] = str(
            payload.get("profile_assignment_note") or ""
        ).strip()
        return payload

    def _decode_paper_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["pdf_path"] = to_repo_relative_path(payload.get("pdf_path"))
        payload["source_path"] = to_repo_relative_path(
            payload.get("source_path") or payload.get("pdf_path")
        )
        payload["source_type"] = (
            str(payload.get("source_type") or "pdf").strip().lower() or "pdf"
        )
        payload["report_path"] = to_repo_relative_path(payload.get("report_path"))
        return payload

    # --- Jobs ---

    def create_job(
        self,
        *,
        mode: str = "auto",
        profile_id: int | None = None,
        profile_mode: str = "auto",
        profile_assignment_status: str = "pending",
        profile_assignment_note: str = "",
        config_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._conn.execute(
            "INSERT INTO jobs (id, status, mode, profile_id, profile_mode, profile_assignment_status, profile_assignment_note, config_snapshot, created_at) "
            "VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                mode,
                profile_id,
                str(profile_mode or "auto"),
                str(profile_assignment_status or "pending"),
                str(profile_assignment_note or ""),
                json.dumps(
                    normalize_config_paths(config_snapshot), ensure_ascii=False
                ),
                now,
            ),
        )
        self._conn.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "progress",
            "current_step",
            "error",
            "paper_title",
            "report_path",
            "started_at",
            "completed_at",
            "profile_id",
            "profile_mode",
            "profile_assignment_status",
            "profile_assignment_note",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "report_path" in updates:
            updates["report_path"] = to_repo_relative_path(updates["report_path"])
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [job_id]
        self._conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._decode_job_row(row)

    def delete_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    def resolve_default_profile_id(self) -> int | None:
        try:
            row = self._conn.execute(
                "SELECT id FROM profiles WHERE name = 'default' ORDER BY id ASC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return int(row["id"])

    def list_profiles(self) -> list[dict[str, Any]]:
        try:
            rows = self._conn.execute(
                "SELECT id, name, description, created_at, last_used_at, paper_count FROM profiles ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_job_row(row)) is not None
        ]

    def list_jobs_for_profile(self, profile_id: int) -> list[dict[str, Any]]:
        if self._table_exists("memory_writebacks"):
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE profile_id = ?
                   OR id IN (
                       SELECT DISTINCT job_id
                       FROM memory_writebacks
                       WHERE profile_id = ?
                   )
                ORDER BY created_at DESC
                """,
                (profile_id, profile_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE profile_id = ? ORDER BY created_at DESC",
                (profile_id,),
            ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_job_row(row)) is not None
        ]

    def list_active_jobs_for_profile(self, profile_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE profile_id = ?
              AND status NOT IN ('completed', 'failed')
            ORDER BY created_at DESC
            """,
            (profile_id,),
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_job_row(row)) is not None
        ]

    def list_active_jobs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status NOT IN ('completed', 'failed') ORDER BY created_at DESC"
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_job_row(row)) is not None
        ]

    def list_report_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = 'completed' AND report_path != '' ORDER BY completed_at DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_job_row(row)) is not None
        ]

    # --- Papers ---

    def save_paper(self, job_id: str, paper_meta: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        paper_id = str(paper_meta.get("paper_id", ""))
        dedupe_key = str(
            paper_meta.get("dedupe_key") or get_candidate_dedupe_key(paper_meta)
        )
        authors = paper_meta.get("authors", [])
        if isinstance(authors, list):
            authors_json = json.dumps(authors, ensure_ascii=False)
        else:
            authors_json = "[]"

        self._conn.execute(
            "INSERT OR REPLACE INTO papers "
            "(job_id, paper_id, dedupe_key, title, venue, pub_date, authors, source, match_track, "
            "selection_reason, pdf_path, source_path, source_type, report_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                paper_id,
                dedupe_key,
                str(paper_meta.get("title", "")),
                str(paper_meta.get("venue", "")),
                str(paper_meta.get("date", paper_meta.get("pub_date", ""))),
                authors_json,
                str(paper_meta.get("source", "")),
                str(paper_meta.get("match_track", "")),
                str(paper_meta.get("selection_reason", "")),
                to_repo_relative_path(str(paper_meta.get("pdf_path", ""))),
                to_repo_relative_path(
                    str(paper_meta.get("source_path") or paper_meta.get("pdf_path", ""))
                ),
                str(paper_meta.get("source_type") or "pdf"),
                to_repo_relative_path(str(paper_meta.get("report_path", ""))),
                now,
            ),
        )
        self._conn.commit()
        return self.get_paper(paper_id)  # type: ignore[return-value]

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        return self._decode_paper_row(row)

    def get_paper_by_db_id(self, row_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE id = ?", (row_id,)
        ).fetchone()
        return self._decode_paper_row(row)

    def get_paper_for_job(
        self, job_id: str, report_path: str = ""
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE job_id = ? OR report_path = ? ORDER BY created_at DESC LIMIT 1",
            (job_id, to_repo_relative_path(report_path)),
        ).fetchone()
        return self._decode_paper_row(row)

    def delete_papers_for_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM papers WHERE job_id = ?", (job_id,))
        self._conn.commit()

    def update_paper_paths(
        self,
        paper_id: str,
        *,
        pdf_path: str | None = None,
        source_path: str | None = None,
        source_type: str | None = None,
        report_path: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {}
        if pdf_path is not None:
            updates["pdf_path"] = to_repo_relative_path(pdf_path)
        if source_path is not None:
            updates["source_path"] = to_repo_relative_path(source_path)
        if source_type is not None:
            updates["source_type"] = str(source_type or "pdf")
        if report_path is not None:
            updates["report_path"] = to_repo_relative_path(report_path)
        if not updates:
            return
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [paper_id]
        self._conn.execute(f"UPDATE papers SET {set_clause} WHERE paper_id = ?", values)
        self._conn.commit()

    def list_papers(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM papers ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_paper_row(row)) is not None
        ]

    def list_paper_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT paper_id FROM papers WHERE paper_id != ''"
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}

    def list_paper_dedupe_keys(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT dedupe_key, paper_id, title, authors, source, venue, pub_date FROM papers"
        ).fetchall()
        keys: set[str] = set()
        for row in rows:
            dedupe_key = str(row[0] or "").strip().lower()
            if dedupe_key:
                keys.add(dedupe_key)
                continue
            paper_meta = {
                "paper_id": row[1],
                "title": row[2],
                "authors": json.loads(row[3] or "[]") if row[3] else [],
                "source": row[4],
                "venue": row[5],
                "date": row[6],
            }
            fallback_key = get_candidate_dedupe_key(paper_meta)
            if fallback_key:
                keys.add(fallback_key)
        return keys

    def search_papers(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM papers WHERE title LIKE ? OR paper_id LIKE ? OR venue LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [
            decoded
            for row in rows
            if (decoded := self._decode_paper_row(row)) is not None
        ]

    def list_profile_activity(
        self, profile_id: int, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not self._table_exists("memory_writebacks"):
            rows = self._conn.execute(
                """
                SELECT
                    jobs.id AS job_id,
                    jobs.status AS job_status,
                    jobs.mode AS job_mode,
                    jobs.progress AS job_progress,
                    jobs.current_step AS job_current_step,
                    jobs.paper_title AS job_paper_title,
                    jobs.report_path AS job_report_path,
                    jobs.created_at AS job_created_at,
                    jobs.started_at AS job_started_at,
                    jobs.completed_at AS job_completed_at,
                    papers.id AS paper_row_id,
                    papers.paper_id AS paper_id,
                    papers.title AS paper_title,
                    papers.venue AS paper_venue,
                    papers.pub_date AS paper_pub_date,
                    papers.pdf_path AS paper_pdf_path,
                    papers.source_path AS paper_source_path,
                    papers.source_type AS paper_source_type,
                    papers.report_path AS paper_report_path,
                    papers.created_at AS paper_created_at
                FROM jobs
                LEFT JOIN papers ON papers.job_id = jobs.id
                WHERE jobs.profile_id = ?
                ORDER BY COALESCE(jobs.completed_at, jobs.created_at) DESC, jobs.created_at DESC
                LIMIT ?
                """,
                (profile_id, limit),
            ).fetchall()
            return [
                {
                    "job_id": str(row["job_id"] or ""),
                    "job_status": str(row["job_status"] or ""),
                    "job_mode": str(row["job_mode"] or ""),
                    "job_progress": int(row["job_progress"] or 0),
                    "job_current_step": str(row["job_current_step"] or ""),
                    "job_paper_title": str(row["job_paper_title"] or ""),
                    "job_report_path": str(row["job_report_path"] or ""),
                    "job_created_at": float(row["job_created_at"] or 0),
                    "job_started_at": float(row["job_started_at"])
                    if row["job_started_at"] is not None
                    else None,
                    "job_completed_at": float(row["job_completed_at"])
                    if row["job_completed_at"] is not None
                    else None,
                    "paper_row_id": int(row["paper_row_id"])
                    if row["paper_row_id"] is not None
                    else None,
                    "paper_id": str(row["paper_id"] or ""),
                    "paper_title": str(row["paper_title"] or ""),
                    "paper_venue": str(row["paper_venue"] or ""),
                    "paper_pub_date": str(row["paper_pub_date"] or ""),
                    "paper_pdf_path": to_repo_relative_path(
                        str(row["paper_pdf_path"] or "")
                    ),
                    "paper_source_path": to_repo_relative_path(
                        str(row["paper_source_path"] or row["paper_pdf_path"] or "")
                    ),
                    "paper_source_type": str(row["paper_source_type"] or "pdf"),
                    "paper_report_path": to_repo_relative_path(
                        str(row["paper_report_path"] or "")
                    ),
                    "paper_created_at": float(row["paper_created_at"]) if row["paper_created_at"] is not None else None,
                }
                for row in rows
            ]

        default_profile_id = self.resolve_default_profile_id()
        include_null_profile = bool(
            default_profile_id is not None
            and int(default_profile_id) == int(profile_id)
        )
        rows = self._conn.execute(
            """
            SELECT
                jobs.id AS job_id,
                jobs.status AS job_status,
                jobs.mode AS job_mode,
                jobs.progress AS job_progress,
                jobs.current_step AS job_current_step,
                jobs.paper_title AS job_paper_title,
                jobs.report_path AS job_report_path,
                jobs.created_at AS job_created_at,
                jobs.started_at AS job_started_at,
                jobs.completed_at AS job_completed_at,
                papers.id AS paper_row_id,
                papers.paper_id AS paper_id,
                papers.title AS paper_title,
                papers.venue AS paper_venue,
                papers.pub_date AS paper_pub_date,
                papers.pdf_path AS paper_pdf_path,
                papers.source_path AS paper_source_path,
                papers.source_type AS paper_source_type,
                papers.report_path AS paper_report_path,
                papers.created_at AS paper_created_at
            FROM memory_writebacks
            JOIN jobs ON jobs.id = memory_writebacks.job_id
            LEFT JOIN papers ON papers.job_id = jobs.id
            WHERE memory_writebacks.profile_id = ?
              AND memory_writebacks.deleted_at IS NULL
              AND (jobs.profile_id = ? OR (? = 1 AND jobs.profile_id IS NULL))
            ORDER BY COALESCE(jobs.completed_at, jobs.created_at) DESC, jobs.created_at DESC
            LIMIT ?
            """,
            (profile_id, profile_id, 1 if include_null_profile else 0, limit),
        ).fetchall()
        payloads: list[dict[str, Any]] = []
        string_defaults = {
            "job_id": "",
            "job_status": "",
            "job_mode": "",
            "job_current_step": "",
            "job_paper_title": "",
            "job_report_path": "",
            "paper_id": "",
            "paper_title": "",
            "paper_venue": "",
            "paper_pub_date": "",
            "paper_pdf_path": "",
            "paper_source_path": "",
            "paper_source_type": "pdf",
            "paper_report_path": "",
        }
        numeric_defaults = {
            "job_progress": 0,
            "job_created_at": 0.0,
        }
        for row in rows:
            payload = dict(row)
            for key, default in string_defaults.items():
                if payload.get(key) is None:
                    payload[key] = default
            for key, default in numeric_defaults.items():
                if payload.get(key) is None:
                    payload[key] = default
            payload["job_report_path"] = to_repo_relative_path(payload.get("job_report_path"))
            payload["paper_pdf_path"] = to_repo_relative_path(payload.get("paper_pdf_path"))
            payload["paper_source_path"] = to_repo_relative_path(
                payload.get("paper_source_path") or payload.get("paper_pdf_path")
            )
            payload["paper_source_type"] = (
                str(payload.get("paper_source_type") or "pdf").strip().lower()
                or "pdf"
            )
            payload["paper_report_path"] = to_repo_relative_path(payload.get("paper_report_path"))
            payloads.append(payload)
        return payloads

    # --- Stats ---

    def get_stats(self) -> dict[str, int]:
        jobs_total = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs_running = self._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status NOT IN ('completed', 'failed')"
        ).fetchone()[0]
        papers_total = self._conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        reports_total = self._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND report_path != ''"
        ).fetchone()[0]
        return {
            "jobs_total": jobs_total,
            "jobs_running": jobs_running,
            "papers_total": papers_total,
            "reports_total": reports_total,
        }
