"""Supervisor archive-and-remove for duplicate or mismatched submissions/reports."""
from __future__ import annotations

import os
from typing import Optional

from app.db import (
    delete_report_row,
    delete_submission_row,
    get_report,
    get_submission,
    insert_deletion_log,
    list_reports_for_source,
)
from app.files_util import archive_file, archive_report_bundle


def _archive_path(entries: list[dict]) -> Optional[str]:
    if not entries:
        return None
    return entries[0].get("archived_to")


def archive_report_record(report_id: int, deleted_by: int, reason: str) -> dict:
    row = get_report(report_id)
    if not row:
        raise ValueError("Report not found.")

    archived = archive_report_bundle(row["pdf_filename"])
    deleted = delete_report_row(report_id)
    log = insert_deletion_log(
        kind="report",
        record_id=report_id,
        filename=row["pdf_filename"],
        reason=reason,
        deleted_by=deleted_by,
        archived_path=_archive_path(archived),
    )
    return {"report": deleted, "archived": archived, "log": log}


def archive_submission_record(submission_id: int, deleted_by: int, reason: str) -> dict:
    row = get_submission(submission_id)
    if not row:
        raise ValueError("Submission not found.")

    archived_submission = []
    stored = row.get("stored_filename")
    if stored:
        try:
            archived_submission.append(archive_file("students", stored))
        except FileNotFoundError:
            pass

    archived_reports = []
    logs = []
    if stored:
        for rep in list_reports_for_source(stored):
            bundle = archive_report_bundle(rep["pdf_filename"])
            archived_reports.extend(bundle)
            deleted_rep = delete_report_row(rep["id"])
            logs.append(
                insert_deletion_log(
                    kind="report",
                    record_id=rep["id"],
                    filename=rep["pdf_filename"],
                    reason=f"{reason} (removed with submission {stored})",
                    deleted_by=deleted_by,
                    archived_path=_archive_path(bundle),
                )
            )

    deleted = delete_submission_row(submission_id)
    sub_log = insert_deletion_log(
        kind="submission",
        record_id=submission_id,
        filename=stored or row.get("original_filename") or f"submission-{submission_id}",
        reason=reason,
        deleted_by=deleted_by,
        archived_path=_archive_path(archived_submission),
    )
    logs.insert(0, sub_log)
    return {
        "submission": deleted,
        "archived_submission": archived_submission,
        "archived_reports": archived_reports,
        "logs": logs,
    }
