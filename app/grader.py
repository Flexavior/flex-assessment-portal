import json
import re
from datetime import datetime
from pathlib import Path


def parse_student_filename(filename):
    stem = Path(filename).stem
    cleaned = re.sub(
        r"[-_]*(Final|Assignment|Submission)[-_]*",
        "_",
        stem,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    student_name = cleaned.replace("_", " ").strip() if cleaned else stem
    return student_name or stem, None


def find_submission_date(content_text, default_date=None):
    patterns = [
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})",
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
    ]
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for pattern in patterns:
        matches = re.findall(pattern, content_text or "", re.IGNORECASE)
        for match in matches:
            try:
                if len(match) == 3 and match[1].isalpha():
                    day, month_name, year = match[0], match[1].lower(), match[2]
                    month = months.get(month_name[:3])
                    if month:
                        return f"{year}{month:02d}{int(day):02d}"
                elif len(match[0]) == 4:
                    year, month, day = match
                    return f"{year}{int(month):02d}{int(day):02d}"
                else:
                    day, month, year = match
                    return f"{year}{int(month):02d}{int(day):02d}"
            except (TypeError, ValueError):
                continue
    if default_date:
        return default_date
    return datetime.now().strftime("%Y%m%d")


def extract_json(raw_text):
    text = (raw_text or "").strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def build_report_filename(student_name, generated_at=None, variant=None):
    """PDF name: {Full_Student_Name}_{YYYYMMDD_HHMMSS}.pdf or with _{variant} for model comparisons."""
    safe_name = re.sub(r"[^\w\- ]", "", student_name).replace(" ", "_").strip("_")
    if not safe_name:
        safe_name = "student"
    when = generated_at or datetime.now()
    timestamp = when.strftime("%Y%m%d_%H%M%S")
    if variant:
        safe_variant = re.sub(r"[^\w\-]+", "_", str(variant)).strip("_")[:48]
        if safe_variant:
            return f"{safe_name}_{timestamp}_{safe_variant}.pdf"
    return f"{safe_name}_{timestamp}.pdf"
