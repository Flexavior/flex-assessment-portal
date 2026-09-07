"""Persist PDF JSON sidecar, SHA-256, and report rows after generation."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from app.config import REPORT_DIR
from app.db import get_report, get_submission_by_stored, get_user, insert_report, insert_review, iso, update_report
from app.integrity import write_sidecar
from app.report import generate_report


def json_path_for(pdf_path: str) -> str:
    return os.path.splitext(pdf_path)[0] + ".json"


def write_result_json(pdf_path: str, result: dict) -> str:
    path = json_path_for(pdf_path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, default=str)
    return path


def finalize_report(report_id: int, result: dict, reviewer_id: int) -> dict:
    """Regenerate PDF/JSON/hash in place and mark report published — no duplicate file."""
    row = get_report(report_id)
    if not row:
        raise ValueError("Report not found.")
    student_name = (result.get("student_name") or row.get("student_name") or "student").strip()
    pdf_name = os.path.basename(row["pdf_filename"])
    pdf_path = os.path.abspath(os.path.join(REPORT_DIR, pdf_name))
    generate_report(
        student_name,
        result.get("submission_date") or "",
        result,
        REPORT_DIR,
        output_filename=pdf_name,
    )
    json_path = write_result_json(pdf_path, result)
    digest = write_sidecar(pdf_path)
    insert_review(report_id, result, reviewer_id)
    updated = update_report(
        report_id,
        pdf_filename=pdf_name,
        json_path=os.path.basename(json_path),
        sha256=digest,
        student_name=student_name,
        total_score_percent=result.get("total_score_percent"),
        generated_at=iso(),
        assignment_id=row.get("assignment_id"),
        status="published",
    )
    return {"report": updated, "sha256": digest}


def infer_student(source_filename: str):
    stored = os.path.basename(source_filename)
    sub = get_submission_by_stored(stored)
    if sub:
        return sub.get("student_user_id"), sub.get("id"), sub.get("assignment_id")
    match = re.match(r"^(\d+)_", stored)
    if match:
        user = get_user(int(match.group(1)))
        if user and user.get("role") == "student":
            return user["id"], None, user.get("assignment_id")
    return None, None, None


def persist_generated_report(
    pdf_path: str,
    result: dict,
    source_filename: Optional[str] = None,
    *,
    is_primary: bool = True,
) -> dict:
    json_path = write_result_json(pdf_path, result)
    digest = write_sidecar(pdf_path)
    student_user_id, submission_id, assignment_id = infer_student(source_filename or "")
    status = "failed" if result.get("error") else "pending_review"
    return insert_report(
        pdf_filename=os.path.basename(pdf_path),
        json_path=os.path.basename(json_path),
        sha256=digest,
        student_name=result.get("student_name"),
        total_score_percent=result.get("total_score_percent"),
        student_user_id=student_user_id,
        submission_id=submission_id,
        assignment_id=assignment_id,
        source_filename=os.path.basename(source_filename) if source_filename else None,
        status=status,
        is_primary=is_primary,
    )
