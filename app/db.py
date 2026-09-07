"""SQLite accounts, assignments, sessions, submissions, reports, and settings."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import DB_PATH

_lock = threading.Lock()
YANGON = timezone(timedelta(hours=6, minutes=30))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row(row) -> Optional[dict]:
    return dict(row) if row else None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    with _lock:
        conn = connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    batch_name TEXT NOT NULL,
                    submission_deadline_at TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(title, batch_name)
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('supervisor','educator','student')),
                    full_name TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    can_self_grade INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_user_id INTEGER,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    FOREIGN KEY(student_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_user_id INTEGER,
                    submission_id INTEGER,
                    source_filename TEXT,
                    pdf_filename TEXT NOT NULL,
                    json_path TEXT,
                    sha256 TEXT,
                    generated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'published',
                    student_name TEXT,
                    total_score_percent REAL,
                    FOREIGN KEY(student_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS report_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL,
                    edited_json TEXT NOT NULL,
                    reviewer_id INTEGER,
                    reviewed_at TEXT NOT NULL,
                    FOREIGN KEY(report_id) REFERENCES reports(id)
                );
                CREATE TABLE IF NOT EXISTS deletion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    record_id INTEGER,
                    filename TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    deleted_by INTEGER NOT NULL,
                    deleted_at TEXT NOT NULL,
                    archived_path TEXT,
                    FOREIGN KEY(deleted_by) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS system_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL
                );
                """
            )
            _ensure_column(conn, "users", "assignment_id", "INTEGER REFERENCES assignments(id)")
            _ensure_column(conn, "submissions", "assignment_id", "INTEGER REFERENCES assignments(id)")
            _ensure_column(conn, "reports", "assignment_id", "INTEGER REFERENCES assignments(id)")
            _ensure_column(conn, "reports", "is_primary", "INTEGER NOT NULL DEFAULT 1")
            conn.execute(
                """
                UPDATE reports
                SET status = 'pending_review'
                WHERE status = 'published'
                  AND id NOT IN (SELECT report_id FROM report_reviews)
                """
            )
            conn.execute(
                """
                INSERT INTO system_log (kind, status, filename, message, actor, created_at)
                SELECT d.kind, 'archived', d.filename, d.reason,
                       COALESCE(NULLIF(u.full_name, ''), u.email, 'user'), d.deleted_at
                FROM deletion_log d
                LEFT JOIN users u ON u.id = d.deleted_by
                WHERE NOT EXISTS (
                    SELECT 1 FROM system_log s
                    WHERE s.kind = d.kind
                      AND s.filename = d.filename
                      AND s.created_at = d.deleted_at
                      AND s.status = 'archived'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def user_count() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = connect()
    try:
        return _row(conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)).fetchone())
    finally:
        conn.close()


def get_user(user_id: int) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT u.*, a.title AS assignment_title, a.batch_name, a.submission_deadline_at
            FROM users u
            LEFT JOIN assignments a ON a.id = u.assignment_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "full_name": user["full_name"],
        "is_active": bool(user["is_active"]),
        "can_self_grade": bool(user["can_self_grade"]),
        "created_at": user["created_at"],
        "assignment_id": user.get("assignment_id"),
        "assignment_title": user.get("assignment_title"),
        "batch_name": user.get("batch_name"),
        "assignment_label": assignment_label(user),
    }


def assignment_label(row: dict) -> str:
    title = (row.get("assignment_title") or row.get("title") or "").strip()
    batch = (row.get("batch_name") or row.get("name") or "").strip()
    if title and batch:
        return f"{title} / {batch}"
    return title or batch


def create_assignment(title: str, batch_name: str, submission_deadline_at: Optional[str], created_by: Optional[int]) -> dict:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO assignments (title, batch_name, submission_deadline_at, is_active, created_by, created_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (title.strip(), batch_name.strip(), submission_deadline_at, created_by, iso()),
            )
            conn.commit()
            return get_assignment(cur.lastrowid)
        finally:
            conn.close()


def get_assignment(assignment_id: int) -> Optional[dict]:
    conn = connect()
    try:
        return _row(conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone())
    finally:
        conn.close()


def list_assignments(active_only: bool = False, search: str = "") -> list[dict]:
    conn = connect()
    try:
        clauses = []
        values = []
        if active_only:
            clauses.append("is_active = 1")
        if search:
            q = f"%{search.strip().lower()}%"
            clauses.append("(lower(title) LIKE ? OR lower(batch_name) LIKE ?)")
            values.extend([q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM assignments {where} ORDER BY is_active DESC, title, batch_name",
            values,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_assignment(assignment_id: int, **fields) -> Optional[dict]:
    allowed = {"title", "batch_name", "submission_deadline_at", "is_active"}
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "is_active":
            value = int(bool(value))
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_assignment(assignment_id)
    values.append(assignment_id)
    with _lock:
        conn = connect()
        try:
            conn.execute(f"UPDATE assignments SET {', '.join(sets)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
    return get_assignment(assignment_id)


def create_user(
    email: str,
    password_hash: str,
    role: str,
    full_name: str,
    can_self_grade: bool = False,
    assignment_id: Optional[int] = None,
) -> dict:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO users (
                    email, password_hash, role, full_name, is_active, can_self_grade, assignment_id, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    email.strip().lower(),
                    password_hash,
                    role,
                    full_name.strip(),
                    int(can_self_grade),
                    assignment_id,
                    iso(),
                ),
            )
            conn.commit()
            return get_user(cur.lastrowid)
        finally:
            conn.close()


def list_users(
    role: Optional[str] = None,
    search: str = "",
    assignment_id: Optional[int] = None,
    unassigned_only: bool = False,
) -> list[dict]:
    conn = connect()
    try:
        clauses = []
        values = []
        if role:
            clauses.append("u.role = ?")
            values.append(role)
        if unassigned_only:
            clauses.append("u.assignment_id IS NULL")
        elif assignment_id is not None:
            clauses.append("u.assignment_id = ?")
            values.append(assignment_id)
        if search:
            raw = search.strip().lower()
            batch_numbers = re.findall(r"\bbatch\s*(\d+)\b", raw)
            for number in batch_numbers:
                clauses.append(
                    "("
                    "lower(COALESCE(a.batch_name, '')) LIKE ? OR "
                    "lower(COALESCE(a.title, '') || ' / ' || COALESCE(a.batch_name, '')) LIKE ?"
                    ")"
                )
                values.extend([f"%batch {number}%", f"%batch {number}%"])

            # Remove explicit batch-number phrases so they do not get re-tokenized.
            cleaned = re.sub(r"\bbatch\s*\d+\b", " ", raw)
            tokens = [t for t in cleaned.split() if t]
            for token in tokens:
                q = f"%{token}%"
                if token.isdigit():
                    clauses.append(
                        "("
                        "lower(COALESCE(a.batch_name, '')) LIKE ? OR "
                        "lower(COALESCE(a.title, '') || ' / ' || COALESCE(a.batch_name, '')) LIKE ?"
                        ")"
                    )
                    values.extend([q, q])
                    continue
                clauses.append(
                    "("
                    "lower(u.full_name) LIKE ? OR "
                    "lower(u.email) LIKE ? OR "
                    "lower(u.role) LIKE ? OR "
                    "lower(COALESCE(a.title, '')) LIKE ? OR "
                    "lower(COALESCE(a.batch_name, '')) LIKE ? OR "
                    "lower(COALESCE(a.title, '') || ' / ' || COALESCE(a.batch_name, '')) LIKE ? OR "
                    "(u.assignment_id IS NULL AND 'unassigned' LIKE ?) OR "
                    "(u.is_active = 1 AND 'active' LIKE ?) OR "
                    "(u.is_active = 0 AND 'inactive' LIKE ?) OR "
                    "(u.can_self_grade = 1 AND ('self-grade' LIKE ? OR 'selfgrade' LIKE ?))"
                    ")"
                )
                values.extend([q, q, q, q, q, q, q, q, q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT u.*, a.title AS assignment_title, a.batch_name, a.submission_deadline_at
            FROM users u
            LEFT JOIN assignments a ON a.id = u.assignment_id
            {where}
            ORDER BY u.role, u.full_name, u.email
            """,
            values,
        ).fetchall()
        return [public_user(dict(r)) for r in rows]
    finally:
        conn.close()


def update_user(user_id: int, **fields) -> Optional[dict]:
    allowed = {"email", "password_hash", "full_name", "is_active", "can_self_grade", "role", "assignment_id"}
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in ("is_active", "can_self_grade"):
            value = int(bool(value))
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_user(user_id)
    values.append(user_id)
    with _lock:
        conn = connect()
        try:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
    return get_user(user_id)


def create_session(token: str, user_id: int, expires_at: datetime):
    with _lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, iso(), iso(expires_at)),
            )
            conn.commit()
        finally:
            conn.close()


def get_session_user(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT u.*, a.title AS assignment_title, a.batch_name, a.submission_deadline_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN assignments a ON a.id = u.assignment_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, iso()),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def delete_session(token: str):
    with _lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    with _lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()


def insert_submission(
    student_user_id: Optional[int],
    original_filename: str,
    stored_filename: str,
    assignment_id: Optional[int] = None,
) -> dict:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO submissions (student_user_id, original_filename, stored_filename, assignment_id, submitted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (student_user_id, original_filename, stored_filename, assignment_id, iso()),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT s.*, u.full_name, a.title AS assignment_title, a.batch_name
                FROM submissions s
                LEFT JOIN users u ON u.id = s.student_user_id
                LEFT JOIN assignments a ON a.id = s.assignment_id
                WHERE s.id = ?
                """,
                (cur.lastrowid,),
            ).fetchone()
            return dict(row)
        finally:
            conn.close()


def get_submission_by_stored(stored_filename: str) -> Optional[dict]:
    conn = connect()
    try:
        return _row(
            conn.execute(
                """
                SELECT s.*, u.full_name, a.title AS assignment_title, a.batch_name
                FROM submissions s
                LEFT JOIN users u ON u.id = s.student_user_id
                LEFT JOIN assignments a ON a.id = s.assignment_id
                WHERE s.stored_filename = ?
                ORDER BY s.id DESC
                LIMIT 1
                """,
                (stored_filename,),
            ).fetchone()
        )
    finally:
        conn.close()


def get_submission(submission_id: int) -> Optional[dict]:
    conn = connect()
    try:
        return _row(
            conn.execute(
                """
                SELECT s.*, u.full_name, a.title AS assignment_title, a.batch_name
                FROM submissions s
                LEFT JOIN users u ON u.id = s.student_user_id
                LEFT JOIN assignments a ON a.id = s.assignment_id
                WHERE s.id = ?
                """,
                (submission_id,),
            ).fetchone()
        )
    finally:
        conn.close()


def update_submission(
    submission_id: int,
    *,
    student_user_id: Optional[int] = None,
    assignment_id: Optional[int] = None,
) -> Optional[dict]:
    with _lock:
        conn = connect()
        try:
            fields = []
            values = []
            if student_user_id is not None:
                fields.append("student_user_id = ?")
                values.append(student_user_id)
            if assignment_id is not None:
                fields.append("assignment_id = ?")
                values.append(assignment_id)
            if not fields:
                return get_submission(submission_id)
            values.append(submission_id)
            conn.execute(
                f"UPDATE submissions SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
            return get_submission(submission_id)
        finally:
            conn.close()


def list_submissions(
    student_user_id: Optional[int] = None,
    limit: int = 200,
    search: str = "",
    assignment_id: Optional[int] = None,
    unassigned_only: bool = False,
) -> list[dict]:
    conn = connect()
    try:
        clauses = []
        values = []
        if student_user_id:
            clauses.append("s.student_user_id = ?")
            values.append(student_user_id)
        if unassigned_only:
            clauses.append("s.assignment_id IS NULL")
        elif assignment_id is not None:
            clauses.append("s.assignment_id = ?")
            values.append(assignment_id)
        if search:
            tokens = [t for t in search.strip().lower().split() if t]
            for token in tokens:
                q = f"%{token}%"
                clauses.append(
                    "("
                    "lower(s.original_filename) LIKE ? OR lower(s.stored_filename) LIKE ? OR "
                    "lower(COALESCE(u.full_name, '')) LIKE ? OR "
                    "lower(COALESCE(a.title, '')) LIKE ? OR lower(COALESCE(a.batch_name, '')) LIKE ?"
                    ")"
                )
                values.extend([q, q, q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT s.*, u.full_name, a.title AS assignment_title, a.batch_name
            FROM submissions s
            LEFT JOIN users u ON u.id = s.student_user_id
            LEFT JOIN assignments a ON a.id = s.assignment_id
            {where}
            ORDER BY s.id DESC
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def submission_count() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    finally:
        conn.close()


def insert_report(
    pdf_filename: str,
    json_path: Optional[str],
    sha256: Optional[str],
    student_name: Optional[str],
    total_score_percent,
    student_user_id: Optional[int] = None,
    submission_id: Optional[int] = None,
    source_filename: Optional[str] = None,
    assignment_id: Optional[int] = None,
    status: str = "published",
    is_primary: bool = True,
) -> dict:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO reports (
                    student_user_id, submission_id, assignment_id, source_filename, pdf_filename, json_path,
                    sha256, generated_at, status, student_name, total_score_percent, is_primary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_user_id,
                    submission_id,
                    assignment_id,
                    source_filename,
                    pdf_filename,
                    json_path,
                    sha256,
                    iso(),
                    status,
                    student_name,
                    total_score_percent,
                    1 if is_primary else 0,
                ),
            )
            conn.commit()
            return get_report(cur.lastrowid)
        finally:
            conn.close()


def get_primary_report(source_filename: str) -> Optional[dict]:
    stored = os.path.basename(source_filename)
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT r.*, u.full_name, a.title AS assignment_title, a.batch_name
            FROM reports r
            LEFT JOIN users u ON u.id = r.student_user_id
            LEFT JOIN assignments a ON a.id = r.assignment_id
            WHERE r.source_filename = ?
              AND r.status IN ('pending_review', 'published')
              AND COALESCE(r.is_primary, 1) = 1
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (stored,),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def get_report(report_id: int) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT r.*, u.full_name, a.title AS assignment_title, a.batch_name
            FROM reports r
            LEFT JOIN users u ON u.id = r.student_user_id
            LEFT JOIN assignments a ON a.id = r.assignment_id
            WHERE r.id = ?
            """,
            (report_id,),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def get_report_by_pdf(pdf_filename: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT r.*, u.full_name, a.title AS assignment_title, a.batch_name
            FROM reports r
            LEFT JOIN users u ON u.id = r.student_user_id
            LEFT JOIN assignments a ON a.id = r.assignment_id
            WHERE r.pdf_filename = ?
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (pdf_filename,),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def list_reports(
    student_user_id: Optional[int] = None,
    limit: int = 200,
    search: str = "",
    assignment_id: Optional[int] = None,
    unassigned_only: bool = False,
    status: Optional[str] = None,
) -> list[dict]:
    conn = connect()
    try:
        clauses = []
        values = []
        if student_user_id:
            clauses.append("r.student_user_id = ?")
            values.append(student_user_id)
        if status:
            clauses.append("r.status = ?")
            values.append(status)
        if unassigned_only:
            clauses.append("r.assignment_id IS NULL")
        elif assignment_id is not None:
            clauses.append("r.assignment_id = ?")
            values.append(assignment_id)
        if search:
            tokens = [t for t in search.strip().lower().split() if t]
            for token in tokens:
                q = f"%{token}%"
                clauses.append(
                    "("
                    "lower(r.pdf_filename) LIKE ? OR lower(COALESCE(r.source_filename, '')) LIKE ? OR "
                    "lower(COALESCE(r.student_name, '')) LIKE ? OR lower(COALESCE(u.full_name, '')) LIKE ? OR "
                    "lower(COALESCE(a.title, '')) LIKE ? OR lower(COALESCE(a.batch_name, '')) LIKE ?"
                    ")"
                )
                values.extend([q, q, q, q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT r.*, u.full_name, a.title AS assignment_title, a.batch_name
            FROM reports r
            LEFT JOIN users u ON u.id = r.student_user_id
            LEFT JOIN assignments a ON a.id = r.assignment_id
            {where}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def report_count(status: Optional[str] = None) -> int:
    conn = connect()
    try:
        if status:
            return conn.execute("SELECT COUNT(*) FROM reports WHERE status = ?", (status,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    finally:
        conn.close()


def update_report(report_id: int, **fields) -> Optional[dict]:
    allowed = {
        "pdf_filename",
        "json_path",
        "sha256",
        "status",
        "student_name",
        "total_score_percent",
        "generated_at",
        "assignment_id",
        "student_user_id",
        "submission_id",
    }
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_report(report_id)
    values.append(report_id)
    with _lock:
        conn = connect()
        try:
            conn.execute(f"UPDATE reports SET {', '.join(sets)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
    return get_report(report_id)


def insert_review(report_id: int, edited_json, reviewer_id: Optional[int]) -> dict:
    payload = edited_json if isinstance(edited_json, str) else json.dumps(edited_json)
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                "INSERT INTO report_reviews (report_id, edited_json, reviewer_id, reviewed_at) VALUES (?, ?, ?, ?)",
                (report_id, payload, reviewer_id, iso()),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM report_reviews WHERE id = ?", (cur.lastrowid,)).fetchone())
        finally:
            conn.close()


def list_reports_for_source(source_filename: str) -> list[dict]:
    stored = os.path.basename(source_filename)
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM reports WHERE source_filename = ? ORDER BY id DESC",
            (stored,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_report_row(report_id: int) -> Optional[dict]:
    with _lock:
        conn = connect()
        try:
            row = _row(conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone())
            if not row:
                return None
            conn.execute("DELETE FROM report_reviews WHERE report_id = ?", (report_id,))
            conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            conn.commit()
            return row
        finally:
            conn.close()


def delete_submission_row(submission_id: int) -> Optional[dict]:
    with _lock:
        conn = connect()
        try:
            row = _row(conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone())
            if not row:
                return None
            conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
            conn.commit()
            return row
        finally:
            conn.close()


def _insert_system_log_row(
    conn: sqlite3.Connection,
    *,
    kind: str,
    status: str = "",
    filename: str = "",
    message: str = "",
    provider: str = "",
    model: str = "",
    actor: str = "system",
    created_at: Optional[str] = None,
) -> dict:
    cur = conn.execute(
        """
        INSERT INTO system_log (kind, status, filename, message, provider, model, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            status or "",
            filename or "",
            message or "",
            provider or "",
            model or "",
            actor or "system",
            created_at or iso(),
        ),
    )
    return dict(conn.execute("SELECT * FROM system_log WHERE id = ?", (cur.lastrowid,)).fetchone())


def insert_system_log(
    *,
    kind: str,
    status: str = "",
    filename: str = "",
    message: str = "",
    provider: str = "",
    model: str = "",
    actor: str = "system",
) -> dict:
    with _lock:
        conn = connect()
        try:
            row = _insert_system_log_row(
                conn,
                kind=kind,
                status=status,
                filename=filename,
                message=message,
                provider=provider,
                model=model,
                actor=actor,
            )
            conn.commit()
            return row
        finally:
            conn.close()


def insert_deletion_log(
    *,
    kind: str,
    record_id: Optional[int],
    filename: str,
    reason: str,
    deleted_by: int,
    archived_path: Optional[str] = None,
) -> dict:
    with _lock:
        conn = connect()
        try:
            when = iso()
            cur = conn.execute(
                """
                INSERT INTO deletion_log (kind, record_id, filename, reason, deleted_by, deleted_at, archived_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, record_id, filename, reason.strip(), deleted_by, when, archived_path),
            )
            actor_row = conn.execute(
                "SELECT full_name, email FROM users WHERE id = ?",
                (deleted_by,),
            ).fetchone()
            actor = "user"
            if actor_row:
                actor = (actor_row["full_name"] or "").strip() or actor_row["email"] or "user"
            _insert_system_log_row(
                conn,
                kind=kind,
                status="archived",
                filename=filename,
                message=reason.strip(),
                actor=actor,
                created_at=when,
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM deletion_log WHERE id = ?", (cur.lastrowid,)).fetchone())
        finally:
            conn.close()


def _system_log_filter(search: str):
    clauses = []
    values = []
    tokens = [t for t in search.strip().lower().split() if t]
    aliases = {
        "done": ("done", "success"),
        "failed": ("failed", "fail", "error"),
        "timeout": ("timed out", "timed_out", "timeout"),
        "timed": ("timed out", "timed_out", "timeout"),
        "timed_out": ("timed out", "timed_out", "timeout"),
    }
    for token in tokens:
        needles = aliases.get(token, (token,))
        parts = []
        for needle in needles:
            q = f"%{needle}%"
            parts.append(
                "("
                "lower(COALESCE(kind, '')) LIKE ? OR "
                "lower(COALESCE(status, '')) LIKE ? OR "
                "lower(COALESCE(filename, '')) LIKE ? OR "
                "lower(COALESCE(message, '')) LIKE ? OR "
                "lower(COALESCE(provider, '')) LIKE ? OR "
                "lower(COALESCE(model, '')) LIKE ? OR "
                "lower(COALESCE(actor, '')) LIKE ?"
                ")"
            )
            values.extend([q, q, q, q, q, q, q])
        clauses.append("(" + " OR ".join(parts) + ")")
    return clauses, values


def _system_log_view(row: dict) -> dict:
    return {
        **row,
        "deleted_at": row.get("created_at"),
        "reason": row.get("message"),
        "deleted_by_name": row.get("actor"),
        "deleted_by_email": "",
    }


def list_system_log(limit: int = 5, offset: int = 0, search: str = "") -> list[dict]:
    conn = connect()
    try:
        clauses, values = _system_log_filter(search)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT * FROM system_log
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, max(1, min(limit, 100)), max(0, offset)],
        ).fetchall()
        return [_system_log_view(dict(r)) for r in rows]
    finally:
        conn.close()


def system_log_count(search: str = "") -> int:
    conn = connect()
    try:
        clauses, values = _system_log_filter(search)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return conn.execute(
            f"SELECT COUNT(*) FROM system_log {where}",
            values,
        ).fetchone()[0]
    finally:
        conn.close()


def list_deletion_log(limit: int = 5, offset: int = 0, search: str = "") -> list[dict]:
    return list_system_log(limit=limit, offset=offset, search=search)


def deletion_log_count(search: str = "") -> int:
    return system_log_count(search=search)
