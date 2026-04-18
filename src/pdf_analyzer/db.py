import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .constants import SQLITE_JOURNAL_MODE, SQLITE_SYNCHRONOUS, SQLITE_TIMEOUT_SECONDS


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=SQLITE_TIMEOUT_SECONDS,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA journal_mode = {SQLITE_JOURNAL_MODE}")
            connection.execute(f"PRAGMA synchronous = {SQLITE_SYNCHRONOUS}")
            self._local.connection = connection
        return connection

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def init_schema(self) -> None:
        conn = self.connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                sha256 TEXT PRIMARY KEY,
                file_size_bytes INTEGER NOT NULL,
                page_count INTEGER,
                canonical_filename TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL,
                absolute_path TEXT NOT NULL UNIQUE,
                relative_path TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(sha256) REFERENCES documents(sha256) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS analysis_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                synthesis_prompt_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(
                    normalized_question,
                    model_name,
                    prompt_version,
                    schema_version,
                    synthesis_prompt_version
                )
            );

            CREATE TABLE IF NOT EXISTS uploads (
                candidate_sha256 TEXT PRIMARY KEY,
                document_sha256 TEXT NOT NULL,
                method TEXT NOT NULL,
                start_page INTEGER,
                end_page INTEGER,
                size_bytes INTEGER NOT NULL,
                local_path TEXT NOT NULL,
                remote_name TEXT,
                remote_uri TEXT,
                uploaded_at TEXT,
                validated_at TEXT,
                expires_at TEXT,
                FOREIGN KEY(document_sha256) REFERENCES documents(sha256) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                config_path TEXT NOT NULL,
                no_gemini INTEGER NOT NULL DEFAULT 0,
                workers INTEGER NOT NULL DEFAULT 1,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                scanned_documents INTEGER NOT NULL DEFAULT 0,
                queued_documents INTEGER NOT NULL DEFAULT 0,
                analyzed_documents INTEGER NOT NULL DEFAULT 0,
                succeeded_documents INTEGER NOT NULL DEFAULT 0,
                failed_documents INTEGER NOT NULL DEFAULT 0,
                skipped_documents INTEGER NOT NULL DEFAULT 0,
                upload_count INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                candidate_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                input_cost_usd REAL NOT NULL DEFAULT 0,
                output_cost_usd REAL NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0,
                report_html_path TEXT,
                report_xlsx_path TEXT,
                FOREIGN KEY(query_id) REFERENCES analysis_queries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS document_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                document_sha256 TEXT NOT NULL,
                run_id INTEGER,
                status TEXT NOT NULL,
                failure_type TEXT,
                error_text TEXT,
                source_path TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                page_count INTEGER,
                responsive INTEGER,
                relevance_score INTEGER,
                summary TEXT,
                people_json TEXT NOT NULL DEFAULT '[]',
                places_json TEXT NOT NULL DEFAULT '[]',
                dates_json TEXT NOT NULL DEFAULT '[]',
                evidence_count INTEGER NOT NULL DEFAULT 0,
                raw_response_json TEXT,
                prompt_json TEXT,
                model_name TEXT NOT NULL,
                analyzed_bytes INTEGER NOT NULL DEFAULT 0,
                compression_method TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                candidate_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                input_cost_usd REAL NOT NULL DEFAULT 0,
                output_cost_usd REAL NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(query_id) REFERENCES analysis_queries(id) ON DELETE CASCADE,
                FOREIGN KEY(document_sha256) REFERENCES documents(sha256) ON DELETE CASCADE,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
                UNIQUE(query_id, document_sha256)
            );

            CREATE TABLE IF NOT EXISTS analysis_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                page_number INTEGER,
                summary TEXT NOT NULL,
                people_json TEXT NOT NULL DEFAULT '[]',
                places_json TEXT NOT NULL DEFAULT '[]',
                dates_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(analysis_id) REFERENCES document_analyses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS project_syntheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL UNIQUE,
                run_id INTEGER,
                status TEXT NOT NULL,
                error_text TEXT,
                answer TEXT,
                key_findings_json TEXT NOT NULL DEFAULT '[]',
                people_json TEXT NOT NULL DEFAULT '[]',
                places_json TEXT NOT NULL DEFAULT '[]',
                dates_json TEXT NOT NULL DEFAULT '[]',
                reasoning_notes TEXT,
                raw_response_json TEXT,
                prompt_json TEXT,
                model_name TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                candidate_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                input_cost_usd REAL NOT NULL DEFAULT 0,
                output_cost_usd REAL NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(query_id) REFERENCES analysis_queries(id) ON DELETE CASCADE,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS gemini_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                query_id INTEGER,
                document_sha256 TEXT,
                call_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error_text TEXT,
                prompt_json TEXT,
                response_json TEXT,
                remote_name TEXT,
                remote_uri TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                candidate_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                input_cost_usd REAL NOT NULL DEFAULT 0,
                output_cost_usd REAL NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0,
                duration_ms REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
                FOREIGN KEY(query_id) REFERENCES analysis_queries(id) ON DELETE SET NULL,
                FOREIGN KEY(document_sha256) REFERENCES documents(sha256) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_document_paths_sha256
                ON document_paths(sha256);
            CREATE INDEX IF NOT EXISTS idx_document_analyses_query_status
                ON document_analyses(query_id, status);
            CREATE INDEX IF NOT EXISTS idx_gemini_call_log_run
                ON gemini_call_log(run_id, created_at);
            """
        )
        conn.commit()

    def set_metadata(self, key: str, value: str) -> None:
        self.connection().execute(
            """
            INSERT INTO metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection().commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.connection().execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def upsert_document(
        self,
        *,
        sha256: str,
        file_size_bytes: int,
        page_count: int | None,
        canonical_filename: str,
        now_iso: str,
    ) -> None:
        self.connection().execute(
            """
            INSERT INTO documents (
                sha256,
                file_size_bytes,
                page_count,
                canonical_filename,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                file_size_bytes = excluded.file_size_bytes,
                page_count = excluded.page_count,
                canonical_filename = excluded.canonical_filename,
                last_seen_at = excluded.last_seen_at
            """,
            (
                sha256,
                file_size_bytes,
                page_count,
                canonical_filename,
                now_iso,
                now_iso,
            ),
        )
        self.connection().commit()

    def upsert_document_path(
        self,
        *,
        sha256: str,
        absolute_path: str,
        relative_path: str,
        original_filename: str,
        now_iso: str,
    ) -> None:
        self.connection().execute(
            """
            INSERT INTO document_paths (
                sha256,
                absolute_path,
                relative_path,
                original_filename,
                discovered_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(absolute_path) DO UPDATE SET
                sha256 = excluded.sha256,
                relative_path = excluded.relative_path,
                original_filename = excluded.original_filename,
                last_seen_at = excluded.last_seen_at
            """,
            (sha256, absolute_path, relative_path, original_filename, now_iso, now_iso),
        )
        self.connection().commit()

    def all_documents(self) -> list[sqlite3.Row]:
        return list(
            self.connection().execute(
                """
                SELECT d.*,
                       (
                           SELECT dp.absolute_path
                           FROM document_paths dp
                           WHERE dp.sha256 = d.sha256
                           ORDER BY LENGTH(dp.relative_path), dp.relative_path
                           LIMIT 1
                       ) AS preferred_path,
                       (
                           SELECT dp.relative_path
                           FROM document_paths dp
                           WHERE dp.sha256 = d.sha256
                           ORDER BY LENGTH(dp.relative_path), dp.relative_path
                           LIMIT 1
                       ) AS preferred_relative_path
                FROM documents d
                ORDER BY d.canonical_filename, d.sha256
                """
            ).fetchall()
        )

    def fetch_document(self, sha256: str) -> sqlite3.Row | None:
        return self.connection().execute(
            """
            SELECT d.*,
                   (
                       SELECT dp.absolute_path
                       FROM document_paths dp
                       WHERE dp.sha256 = d.sha256
                       ORDER BY LENGTH(dp.relative_path), dp.relative_path
                       LIMIT 1
                   ) AS preferred_path,
                   (
                       SELECT dp.relative_path
                       FROM document_paths dp
                       WHERE dp.sha256 = d.sha256
                       ORDER BY LENGTH(dp.relative_path), dp.relative_path
                       LIMIT 1
                   ) AS preferred_relative_path
            FROM documents d
            WHERE d.sha256 = ?
            """,
            (sha256,),
        ).fetchone()

    def get_or_create_query(
        self,
        *,
        question: str,
        normalized_question: str,
        model_name: str,
        prompt_version: str,
        schema_version: str,
        synthesis_prompt_version: str,
        now_iso: str,
    ) -> int:
        conn = self.connection()
        conn.execute(
            """
            INSERT INTO analysis_queries (
                question,
                normalized_question,
                model_name,
                prompt_version,
                schema_version,
                synthesis_prompt_version,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                normalized_question,
                model_name,
                prompt_version,
                schema_version,
                synthesis_prompt_version
            ) DO NOTHING
            """,
            (
                question,
                normalized_question,
                model_name,
                prompt_version,
                schema_version,
                synthesis_prompt_version,
                now_iso,
            ),
        )
        row = conn.execute(
            """
            SELECT id
            FROM analysis_queries
            WHERE normalized_question = ?
              AND model_name = ?
              AND prompt_version = ?
              AND schema_version = ?
              AND synthesis_prompt_version = ?
            """,
            (
                normalized_question,
                model_name,
                prompt_version,
                schema_version,
                synthesis_prompt_version,
            ),
        ).fetchone()
        conn.commit()
        assert row is not None
        return int(row["id"])

    def start_run(
        self,
        *,
        query_id: int,
        config_path: str,
        no_gemini: bool,
        workers: int,
        started_at: str,
    ) -> int:
        conn = self.connection()
        cursor = conn.execute(
            """
            INSERT INTO runs (query_id, config_path, no_gemini, workers, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (query_id, config_path, int(no_gemini), workers, started_at),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def finalize_run(
        self,
        *,
        run_id: int,
        completed_at: str,
        scanned_documents: int,
        queued_documents: int,
        analyzed_documents: int,
        succeeded_documents: int,
        failed_documents: int,
        skipped_documents: int,
        upload_count: int,
        report_html_path: str,
        report_xlsx_path: str,
    ) -> None:
        summary = self.connection().execute(
            """
            SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(candidate_tokens), 0) AS candidate_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(input_cost_usd), 0) AS input_cost_usd,
                   COALESCE(SUM(output_cost_usd), 0) AS output_cost_usd,
                   COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd
            FROM gemini_call_log
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        self.connection().execute(
            """
            UPDATE runs
            SET completed_at = ?,
                scanned_documents = ?,
                queued_documents = ?,
                analyzed_documents = ?,
                succeeded_documents = ?,
                failed_documents = ?,
                skipped_documents = ?,
                upload_count = ?,
                prompt_tokens = ?,
                candidate_tokens = ?,
                total_tokens = ?,
                input_cost_usd = ?,
                output_cost_usd = ?,
                total_cost_usd = ?,
                report_html_path = ?,
                report_xlsx_path = ?
            WHERE id = ?
            """,
            (
                completed_at,
                scanned_documents,
                queued_documents,
                analyzed_documents,
                succeeded_documents,
                failed_documents,
                skipped_documents,
                upload_count,
                int(summary["prompt_tokens"]),
                int(summary["candidate_tokens"]),
                int(summary["total_tokens"]),
                float(summary["input_cost_usd"]),
                float(summary["output_cost_usd"]),
                float(summary["total_cost_usd"]),
                report_html_path,
                report_xlsx_path,
                run_id,
            ),
        )
        self.connection().commit()

    def latest_run(self) -> sqlite3.Row | None:
        return self.connection().execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def calculate_run_summary(
        self,
        *,
        run_id: int,
        scanned_documents: int,
        queued_documents: int,
        analyzed_documents: int,
        succeeded_documents: int,
        failed_documents: int,
        skipped_documents: int,
        upload_count: int,
    ) -> dict[str, Any]:
        row = self.connection().execute(
            """
            SELECT *
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        summary = self.connection().execute(
            """
            SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(candidate_tokens), 0) AS candidate_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(input_cost_usd), 0) AS input_cost_usd,
                   COALESCE(SUM(output_cost_usd), 0) AS output_cost_usd,
                   COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd
            FROM gemini_call_log
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        assert row is not None
        payload = dict(row)
        payload.update(
            {
                "scanned_documents": scanned_documents,
                "queued_documents": queued_documents,
                "analyzed_documents": analyzed_documents,
                "succeeded_documents": succeeded_documents,
                "failed_documents": failed_documents,
                "skipped_documents": skipped_documents,
                "upload_count": upload_count,
                "prompt_tokens": int(summary["prompt_tokens"]),
                "candidate_tokens": int(summary["candidate_tokens"]),
                "total_tokens": int(summary["total_tokens"]),
                "input_cost_usd": float(summary["input_cost_usd"]),
                "output_cost_usd": float(summary["output_cost_usd"]),
                "total_cost_usd": float(summary["total_cost_usd"]),
            }
        )
        return payload

    def upsert_upload(
        self,
        *,
        candidate_sha256: str,
        document_sha256: str,
        method: str,
        start_page: int | None,
        end_page: int | None,
        size_bytes: int,
        local_path: str,
        remote_name: str | None,
        remote_uri: str | None,
        uploaded_at: str | None,
        validated_at: str | None,
        expires_at: str | None,
    ) -> None:
        self.connection().execute(
            """
            INSERT INTO uploads (
                candidate_sha256,
                document_sha256,
                method,
                start_page,
                end_page,
                size_bytes,
                local_path,
                remote_name,
                remote_uri,
                uploaded_at,
                validated_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_sha256) DO UPDATE SET
                document_sha256 = excluded.document_sha256,
                method = excluded.method,
                start_page = excluded.start_page,
                end_page = excluded.end_page,
                size_bytes = excluded.size_bytes,
                local_path = excluded.local_path,
                remote_name = excluded.remote_name,
                remote_uri = excluded.remote_uri,
                uploaded_at = COALESCE(excluded.uploaded_at, uploads.uploaded_at),
                validated_at = COALESCE(excluded.validated_at, uploads.validated_at),
                expires_at = COALESCE(excluded.expires_at, uploads.expires_at)
            """,
            (
                candidate_sha256,
                document_sha256,
                method,
                start_page,
                end_page,
                size_bytes,
                local_path,
                remote_name,
                remote_uri,
                uploaded_at,
                validated_at,
                expires_at,
            ),
        )
        self.connection().commit()

    def fetch_upload(self, candidate_sha256: str) -> sqlite3.Row | None:
        return self.connection().execute(
            "SELECT * FROM uploads WHERE candidate_sha256 = ?",
            (candidate_sha256,),
        ).fetchone()

    def successful_analysis_exists(self, query_id: int, document_sha256: str) -> bool:
        row = self.connection().execute(
            """
            SELECT 1
            FROM document_analyses
            WHERE query_id = ?
              AND document_sha256 = ?
              AND status = 'succeeded'
            """,
            (query_id, document_sha256),
        ).fetchone()
        return row is not None

    def upsert_document_analysis(self, payload: dict[str, Any]) -> int:
        conn = self.connection()
        conn.execute(
            """
            INSERT INTO document_analyses (
                query_id,
                document_sha256,
                run_id,
                status,
                failure_type,
                error_text,
                source_path,
                source_filename,
                page_count,
                responsive,
                relevance_score,
                summary,
                people_json,
                places_json,
                dates_json,
                evidence_count,
                raw_response_json,
                prompt_json,
                model_name,
                analyzed_bytes,
                compression_method,
                prompt_tokens,
                candidate_tokens,
                total_tokens,
                input_cost_usd,
                output_cost_usd,
                total_cost_usd,
                started_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_id, document_sha256) DO UPDATE SET
                run_id = excluded.run_id,
                status = excluded.status,
                failure_type = excluded.failure_type,
                error_text = excluded.error_text,
                source_path = excluded.source_path,
                source_filename = excluded.source_filename,
                page_count = excluded.page_count,
                responsive = excluded.responsive,
                relevance_score = excluded.relevance_score,
                summary = excluded.summary,
                people_json = excluded.people_json,
                places_json = excluded.places_json,
                dates_json = excluded.dates_json,
                evidence_count = excluded.evidence_count,
                raw_response_json = excluded.raw_response_json,
                prompt_json = excluded.prompt_json,
                model_name = excluded.model_name,
                analyzed_bytes = excluded.analyzed_bytes,
                compression_method = excluded.compression_method,
                prompt_tokens = excluded.prompt_tokens,
                candidate_tokens = excluded.candidate_tokens,
                total_tokens = excluded.total_tokens,
                input_cost_usd = excluded.input_cost_usd,
                output_cost_usd = excluded.output_cost_usd,
                total_cost_usd = excluded.total_cost_usd,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                payload["query_id"],
                payload["document_sha256"],
                payload.get("run_id"),
                payload["status"],
                payload.get("failure_type"),
                payload.get("error_text"),
                payload["source_path"],
                payload["source_filename"],
                payload.get("page_count"),
                payload.get("responsive"),
                payload.get("relevance_score"),
                payload.get("summary"),
                json.dumps(payload.get("people", []), sort_keys=True),
                json.dumps(payload.get("places", []), sort_keys=True),
                json.dumps(payload.get("dates", []), sort_keys=True),
                payload.get("evidence_count", 0),
                json.dumps(payload.get("raw_response"), sort_keys=True)
                if payload.get("raw_response") is not None
                else None,
                json.dumps(payload.get("prompt"), sort_keys=True)
                if payload.get("prompt") is not None
                else None,
                payload["model_name"],
                payload.get("analyzed_bytes", 0),
                payload.get("compression_method"),
                payload.get("prompt_tokens", 0),
                payload.get("candidate_tokens", 0),
                payload.get("total_tokens", 0),
                payload.get("input_cost_usd", 0.0),
                payload.get("output_cost_usd", 0.0),
                payload.get("total_cost_usd", 0.0),
                payload["started_at"],
                payload.get("completed_at"),
            ),
        )
        row = conn.execute(
            """
            SELECT id
            FROM document_analyses
            WHERE query_id = ? AND document_sha256 = ?
            """,
            (payload["query_id"], payload["document_sha256"]),
        ).fetchone()
        conn.commit()
        assert row is not None
        return int(row["id"])

    def replace_analysis_evidence(self, analysis_id: int, evidence_items: list[dict[str, Any]]) -> None:
        conn = self.connection()
        conn.execute("DELETE FROM analysis_evidence WHERE analysis_id = ?", (analysis_id,))
        for ordinal, item in enumerate(evidence_items, start=1):
            conn.execute(
                """
                INSERT INTO analysis_evidence (
                    analysis_id,
                    ordinal,
                    page_number,
                    summary,
                    people_json,
                    places_json,
                    dates_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    ordinal,
                    item.get("page_number"),
                    item["summary"],
                    json.dumps(item.get("people", []), sort_keys=True),
                    json.dumps(item.get("places", []), sort_keys=True),
                    json.dumps(item.get("dates", []), sort_keys=True),
                ),
            )
        conn.commit()

    def upsert_synthesis(self, payload: dict[str, Any]) -> None:
        self.connection().execute(
            """
            INSERT INTO project_syntheses (
                query_id,
                run_id,
                status,
                error_text,
                answer,
                key_findings_json,
                people_json,
                places_json,
                dates_json,
                reasoning_notes,
                raw_response_json,
                prompt_json,
                model_name,
                prompt_tokens,
                candidate_tokens,
                total_tokens,
                input_cost_usd,
                output_cost_usd,
                total_cost_usd,
                started_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_id) DO UPDATE SET
                run_id = excluded.run_id,
                status = excluded.status,
                error_text = excluded.error_text,
                answer = excluded.answer,
                key_findings_json = excluded.key_findings_json,
                people_json = excluded.people_json,
                places_json = excluded.places_json,
                dates_json = excluded.dates_json,
                reasoning_notes = excluded.reasoning_notes,
                raw_response_json = excluded.raw_response_json,
                prompt_json = excluded.prompt_json,
                model_name = excluded.model_name,
                prompt_tokens = excluded.prompt_tokens,
                candidate_tokens = excluded.candidate_tokens,
                total_tokens = excluded.total_tokens,
                input_cost_usd = excluded.input_cost_usd,
                output_cost_usd = excluded.output_cost_usd,
                total_cost_usd = excluded.total_cost_usd,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                payload["query_id"],
                payload.get("run_id"),
                payload["status"],
                payload.get("error_text"),
                payload.get("answer"),
                json.dumps(payload.get("key_findings", []), sort_keys=True),
                json.dumps(payload.get("people", []), sort_keys=True),
                json.dumps(payload.get("places", []), sort_keys=True),
                json.dumps(payload.get("dates", []), sort_keys=True),
                payload.get("reasoning_notes"),
                json.dumps(payload.get("raw_response"), sort_keys=True)
                if payload.get("raw_response") is not None
                else None,
                json.dumps(payload.get("prompt"), sort_keys=True)
                if payload.get("prompt") is not None
                else None,
                payload["model_name"],
                payload.get("prompt_tokens", 0),
                payload.get("candidate_tokens", 0),
                payload.get("total_tokens", 0),
                payload.get("input_cost_usd", 0.0),
                payload.get("output_cost_usd", 0.0),
                payload.get("total_cost_usd", 0.0),
                payload["started_at"],
                payload.get("completed_at"),
            ),
        )
        self.connection().commit()

    def fetch_synthesis(self, query_id: int) -> sqlite3.Row | None:
        return self.connection().execute(
            "SELECT * FROM project_syntheses WHERE query_id = ?",
            (query_id,),
        ).fetchone()

    def insert_gemini_call_log(self, payload: dict[str, Any]) -> None:
        self.connection().execute(
            """
            INSERT INTO gemini_call_log (
                run_id,
                query_id,
                document_sha256,
                call_type,
                status,
                error_text,
                prompt_json,
                response_json,
                remote_name,
                remote_uri,
                prompt_tokens,
                candidate_tokens,
                total_tokens,
                input_cost_usd,
                output_cost_usd,
                total_cost_usd,
                duration_ms,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("run_id"),
                payload.get("query_id"),
                payload.get("document_sha256"),
                payload["call_type"],
                payload["status"],
                payload.get("error_text"),
                json.dumps(payload.get("prompt"), sort_keys=True)
                if payload.get("prompt") is not None
                else None,
                json.dumps(payload.get("response"), sort_keys=True)
                if payload.get("response") is not None
                else None,
                payload.get("remote_name"),
                payload.get("remote_uri"),
                payload.get("prompt_tokens", 0),
                payload.get("candidate_tokens", 0),
                payload.get("total_tokens", 0),
                payload.get("input_cost_usd", 0.0),
                payload.get("output_cost_usd", 0.0),
                payload.get("total_cost_usd", 0.0),
                payload.get("duration_ms"),
                payload["created_at"],
            ),
        )
        self.connection().commit()

    def report_evidence_rows(self, query_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection().execute(
                """
                SELECT da.id AS analysis_id,
                       da.document_sha256,
                       da.source_path,
                       da.source_filename,
                       da.summary AS document_summary,
                       da.relevance_score,
                       da.people_json AS document_people_json,
                       da.places_json AS document_places_json,
                       da.dates_json AS document_dates_json,
                       ae.ordinal,
                       ae.page_number,
                       ae.summary AS evidence_summary,
                       ae.people_json,
                       ae.places_json,
                       ae.dates_json
                FROM document_analyses da
                JOIN analysis_evidence ae ON ae.analysis_id = da.id
                WHERE da.query_id = ?
                  AND da.status = 'succeeded'
                  AND da.responsive = 1
                ORDER BY da.source_filename, ae.ordinal
                """
            , (query_id,)).fetchall()
        )

    def report_document_rows(self, query_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection().execute(
                """
                SELECT d.sha256,
                       d.file_size_bytes,
                       d.page_count AS stored_page_count,
                       d.canonical_filename,
                       (
                           SELECT dp.absolute_path
                           FROM document_paths dp
                           WHERE dp.sha256 = d.sha256
                           ORDER BY LENGTH(dp.relative_path), dp.relative_path
                           LIMIT 1
                       ) AS source_path,
                       (
                           SELECT dp.relative_path
                           FROM document_paths dp
                           WHERE dp.sha256 = d.sha256
                           ORDER BY LENGTH(dp.relative_path), dp.relative_path
                           LIMIT 1
                       ) AS relative_path,
                       da.status,
                       da.failure_type,
                       da.error_text,
                       da.responsive,
                       da.relevance_score,
                       da.summary,
                       da.people_json,
                       da.places_json,
                       da.dates_json,
                       da.evidence_count,
                       da.completed_at
                FROM documents d
                LEFT JOIN document_analyses da
                  ON da.document_sha256 = d.sha256
                 AND da.query_id = ?
                ORDER BY d.canonical_filename, d.sha256
                """
            , (query_id,)).fetchall()
        )

    def report_failure_rows(self, query_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection().execute(
                """
                SELECT d.sha256,
                       da.source_filename,
                       da.source_path,
                       da.status,
                       da.failure_type,
                       da.error_text,
                       da.completed_at
                FROM document_analyses da
                JOIN documents d ON d.sha256 = da.document_sha256
                WHERE da.query_id = ?
                  AND da.status != 'succeeded'
                ORDER BY da.source_filename, da.document_sha256
                """
            , (query_id,)).fetchall()
        )

    def count_uploads_validated_this_run(self, run_id: int) -> int:
        row = self.connection().execute(
            """
            SELECT COUNT(*)
            FROM gemini_call_log
            WHERE run_id = ?
              AND call_type = 'upload'
              AND status = 'succeeded'
            """,
            (run_id,),
        ).fetchone()
        return 0 if row is None else int(row[0])
