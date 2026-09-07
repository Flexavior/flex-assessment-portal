"""PDF assessment reports named {student_name}_{YYYYMMDD_HHMMSS}.pdf."""
import os
import re
import xml.sax.saxutils as xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import REPORT_DIR
from app.grader import build_report_filename
from app.word_limits import evaluate_word_count

NAVY = colors.HexColor("#1a5276")
HEADER_BG = colors.HexColor("#d4e6f1")
ROW_ALT = colors.HexColor("#f4f6f7")
WARN_ORANGE = colors.HexColor("#d35400")
WARN_RED = colors.HexColor("#c0392b")


def _esc(value):
    return xml_escape.escape(str(value if value is not None else ""))


def _p(text, style):
    return Paragraph(_esc(text).replace("\n", "<br/>"), style)


def _list_items(items):
    if not items:
        return []
    if isinstance(items, str):
        return [items]
    return [str(item) for item in items if item]


def generate_report(
    student_name,
    date_str,
    grading_result,
    output_dir=None,
    filename_variant=None,
    output_filename=None,
):
    """
    Write a PDF report. Returns the output path.
    grading_result may include an 'error' key for failed LLM JSON.
    When output_filename is set, overwrite that PDF (approve/finalize flow).
    """
    output_dir = output_dir or REPORT_DIR
    os.makedirs(output_dir, exist_ok=True)

    if output_filename:
        filename = os.path.basename(output_filename)
        filepath = os.path.join(output_dir, filename)
    else:
        filename = build_report_filename(student_name, variant=filename_variant)
        filepath = os.path.join(output_dir, filename)

    display_date = date_str
    if re.fullmatch(r"\d{8}", str(date_str or "")):
        display_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], fontSize=18, textColor=NAVY, spaceAfter=8
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=NAVY,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontSize=9, leading=12)
    cell_style = ParagraphStyle(
        "Cell", parent=styles["BodyText"], fontSize=8, leading=11, alignment=TA_LEFT
    )
    header_cell = ParagraphStyle(
        "HeaderCell", parent=cell_style, textColor=NAVY, fontName="Helvetica-Bold"
    )

    story = []
    story.append(Paragraph("Assessment Report", title_style))
    story.append(_p(f"Student: {student_name}", body_style))
    story.append(_p(f"Submission date: {display_date}", body_style))
    story.append(Spacer(1, 10))

    result = grading_result or {}

    if result.get("error"):
        story.append(Paragraph("Grading could not be completed automatically", heading_style))
        story.append(_p(result.get("error"), body_style))
        raw = result.get("raw_output") or ""
        if raw:
            story.append(Paragraph("Raw model output (for manual review)", heading_style))
            story.append(_p(raw[:3000], body_style))
        story.append(Spacer(1, 16))
        story.append(_p("Automated Grading System by FLEXAVIOR", styles["Italic"]))
        doc.build(story)
        return filepath

    percent = result.get("total_score_percent", "N/A")
    out_of = result.get("total_score_out_of", "")
    score_line = f"Overall score: {percent}%"
    if out_of not in ("", None, 0):
        score_line += f"  (raw total out of {out_of})"
    story.append(Paragraph(score_line, heading_style))

    word_count = result.get("submission_word_count") or result.get("extraction_word_count")
    wc = result.get("word_count") or evaluate_word_count(word_count)
    if wc.get("message"):
        warn_style = ParagraphStyle(
            "WordCountWarn",
            parent=body_style,
            fontSize=10,
            leading=13,
            textColor=WARN_RED if wc.get("level") == "over_limit" else WARN_ORANGE if wc.get("warn") else NAVY,
            spaceBefore=4,
            spaceAfter=6,
        )
        story.append(_p(wc["message"], warn_style))

    story.append(Spacer(1, 6))

    coverage = result.get("assignment_coverage") or []
    if coverage:
        story.append(Paragraph("Assignment coverage", heading_style))
        rows = [[
            Paragraph("Topic", header_cell),
            Paragraph("Covered", header_cell),
            Paragraph("Note", header_cell),
        ]]
        for item in coverage:
            covered = item.get("covered")
            mark = "Yes" if covered is True else "No" if covered is False else str(covered)
            label = item.get("topic") or item.get("question") or ""
            rows.append([
                _p(label, cell_style),
                _p(mark, cell_style),
                _p(item.get("coverage_note", ""), cell_style),
            ])
        table = Table(rows, colWidths=[2.8 * inch, 0.8 * inch, 3.2 * inch])
        table.setStyle(_table_style(len(rows)))
        story.append(table)

    criteria = result.get("criteria_scores") or []
    if criteria:
        story.append(Paragraph("Criteria scores", heading_style))
        rows = [[
            Paragraph("Criterion", header_cell),
            Paragraph("Score", header_cell),
            Paragraph("Max", header_cell),
            Paragraph("Justification", header_cell),
        ]]
        for item in criteria:
            rows.append([
                _p(item.get("criterion", ""), cell_style),
                _p(item.get("score", ""), cell_style),
                _p(item.get("max_score", ""), cell_style),
                _p(item.get("justification", ""), cell_style),
            ])
        table = Table(rows, colWidths=[2.2 * inch, 0.7 * inch, 0.6 * inch, 3.3 * inch])
        table.setStyle(_table_style(len(rows)))
        story.append(table)

    def _bullets(title, items):
        values = _list_items(items)
        if not values:
            return
        story.append(Paragraph(title, heading_style))
        for value in values:
            story.append(_p(f"• {value}", body_style))

    _bullets("Missing or partial questions", result.get("missing_or_partial"))
    _bullets("Contradictions with course material", result.get("contradictions"))
    _bullets("Strengths", result.get("strengths"))
    _bullets("Areas for improvement", result.get("areas_for_improvement"))

    feedback = result.get("feedback") or ""
    if feedback:
        story.append(Paragraph("Overall feedback", heading_style))
        story.append(_p(feedback, body_style))

    story.append(Spacer(1, 16))
    story.append(_p("Automated Grading System by FLEXAVIOR", styles["Italic"]))
    doc.build(story)
    return filepath


def _table_style(row_count):
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bfc9ca")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, row_count):
        if i % 2 == 0:
            commands.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    return TableStyle(commands)
