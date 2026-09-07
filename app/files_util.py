"""Safe listing, archive-then-remove, and zip of data folders."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import (
    ARCHIVE_DIR,
    REPORT_DIR,
    RUBRIC_DIR,
    SLIDES_DIR,
    STUDENT_DIR,
    TEXTBOOK_DIR,
)

FOLDERS = {
    "students": STUDENT_DIR,
    "reports": REPORT_DIR,
    "slides": SLIDES_DIR,
    "textbook": TEXTBOOK_DIR,
    "rubric": RUBRIC_DIR,
}


def folder_root(kind: str) -> str:
    if kind not in FOLDERS:
        raise ValueError(f"Unknown folder: {kind}")
    return os.path.abspath(FOLDERS[kind])


def safe_join(kind: str, filename: str) -> str:
    root = folder_root(kind)
    path = os.path.abspath(os.path.join(root, os.path.basename(filename)))
    if not path.startswith(root):
        raise ValueError("Invalid path.")
    return path


def list_files(kind: str) -> list[dict]:
    root = folder_root(kind)
    os.makedirs(root, exist_ok=True)
    items = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        if kind == "reports" and not name.lower().endswith(".pdf"):
            continue
        stat = os.stat(path)
        items.append({"name": name, "size": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return items


def archive_file(kind: str, filename: str) -> dict:
    """Move a live file into data/archive/<timestamp>/<kind>/ — never hard-delete first."""
    if kind not in FOLDERS:
        raise ValueError(f"Cannot archive folder kind: {kind}")
    src = safe_join(kind, filename)
    if not os.path.isfile(src):
        raise FileNotFoundError(filename)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(ARCHIVE_DIR, stamp, kind)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.move(src, dest)
    return {"archived_to": dest, "name": os.path.basename(src), "kind": kind}


def archive_report_bundle(pdf_filename: str) -> list[dict]:
    """Archive PDF plus JSON sidecar and SHA-256 file into one archive folder."""
    archived = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(ARCHIVE_DIR, stamp, "reports")
    os.makedirs(dest_dir, exist_ok=True)

    base = os.path.basename(pdf_filename)
    names = [base, os.path.splitext(base)[0] + ".json", base + ".sha256"]
    for name in names:
        src = safe_join("reports", name)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(dest_dir, os.path.basename(src))
        shutil.move(src, dest)
        archived.append({"archived_to": dest, "name": os.path.basename(src), "kind": "reports"})
    return archived


def zip_selected(kind: str, filenames: list[str]) -> bytes:
    if kind not in ("students", "reports"):
        raise ValueError("Zip is only available for students and reports folders.")
    if not filenames:
        raise ValueError("No files selected.")
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        used = set()
        for name in filenames:
            path = safe_join(kind, name)
            if not os.path.isfile(path):
                raise FileNotFoundError(name)
            arc = os.path.basename(path)
            if arc in used:
                continue
            used.add(arc)
            zf.write(path, arcname=f"{kind}/{arc}")
    return buf.getvalue()
