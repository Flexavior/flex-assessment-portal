"""Live brief loaders: assignment, rubric, grading policy."""
import os

from app.config import (
    ASSIGNMENT_DIR,
    GRADING_POLICY_PATH,
    RUBRIC_DIR,
    SLIDES_DIR,
    TEXTBOOK_DIR,
)
from app.submission import read_docx, read_pdf, read_ppt, read_txt

TEXT_EXTENSIONS = {".txt", ".md"}
DOCX_EXTENSIONS = {".docx", ".doc"}
PDF_EXTENSIONS = {".pdf"}
PPT_EXTENSIONS = {".pptx", ".ppt"}


def read_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in DOCX_EXTENSIONS:
        return read_docx(path)
    if ext in PDF_EXTENSIONS:
        return read_pdf(path)
    if ext in PPT_EXTENSIONS:
        return read_ppt(path)
    if ext in TEXT_EXTENSIONS:
        return read_txt(path)
    return ""


def load_dir_text(directory, prefer_extensions=None):
    if not os.path.isdir(directory):
        return ""
    names = [
        name for name in sorted(os.listdir(directory))
        if os.path.isfile(os.path.join(directory, name)) and not name.startswith(".")
    ]
    if prefer_extensions:
        preferred = [
            name for name in names
            if os.path.splitext(name)[1].lower() in prefer_extensions
        ]
        if preferred:
            names = preferred
    parts = []
    for name in names:
        text = read_file(os.path.join(directory, name)).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def load_assignment_text():
    return load_dir_text(ASSIGNMENT_DIR, prefer_extensions=TEXT_EXTENSIONS)


def load_rubric_text():
    return load_dir_text(RUBRIC_DIR)


def load_grading_policy():
    if os.path.isfile(GRADING_POLICY_PATH):
        return read_txt(GRADING_POLICY_PATH).strip()
    return ""


def load_course_material():
    return "\n\n".join(
        part for part in (load_dir_text(TEXTBOOK_DIR), load_dir_text(SLIDES_DIR)) if part
    )
