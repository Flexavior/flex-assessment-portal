"""SHA-256 sidecar hashes for generated PDF reports."""
from __future__ import annotations

import hashlib
import os
from typing import Optional


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_path(pdf_path: str) -> str:
    return pdf_path + ".sha256"


def write_sidecar(pdf_path: str, digest: Optional[str] = None) -> str:
    digest = digest or sha256_file(pdf_path)
    with open(sidecar_path(pdf_path), "w", encoding="utf-8") as handle:
        handle.write(digest + "\n")
    return digest


def read_sidecar(pdf_path: str) -> Optional[str]:
    path = sidecar_path(pdf_path)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip() or None


def verify_pdf(pdf_path: str, expected: Optional[str] = None) -> dict:
    if not os.path.isfile(pdf_path):
        return {"ok": False, "breach": True, "detail": "PDF missing on disk."}
    current = sha256_file(pdf_path)
    recorded = expected or read_sidecar(pdf_path)
    if not recorded:
        return {
            "ok": False,
            "breach": False,
            "detail": "No recorded hash to compare.",
            "current": current,
            "recorded": None,
        }
    match = current.lower() == recorded.lower()
    return {
        "ok": match,
        "breach": not match,
        "current": current,
        "recorded": recorded,
        "detail": "Hash matches." if match else "PDF hash does not match recorded value. File may have been modified.",
    }
