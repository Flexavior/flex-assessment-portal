"""Tests for report approval (in-place finalize, no duplicate PDF)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.config import REPORT_DIR
from app.db import init_db, insert_report, iso, get_report
from app.reports_persist import finalize_report, write_result_json


class FinalizeReportTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._report_dir = os.path.join(self._tmpdir.name, "reports")
        os.makedirs(self._report_dir, exist_ok=True)
        init_db()

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("app.reports_persist.REPORT_DIR")
    @patch("app.report.REPORT_DIR")
    def test_finalize_overwrites_same_pdf(self, mock_report_dir, mock_persist_dir):
        mock_report_dir.__str__ = lambda self: self._report_dir  # unused
        mock_report_dir = self._report_dir
        mock_persist_dir = self._report_dir

        with patch("app.reports_persist.REPORT_DIR", self._report_dir), patch(
            "app.report.REPORT_DIR", self._report_dir
        ):
            pdf_name = "Test_Student_20260820_120000.pdf"
            pdf_path = os.path.join(self._report_dir, pdf_name)
            with open(pdf_path, "wb") as handle:
                handle.write(b"%PDF-1.4 original")

            row = insert_report(
                pdf_filename=pdf_name,
                json_path="test.json",
                sha256="abc",
                student_name="Test Student",
                total_score_percent=50.0,
                status="pending_review",
            )
            write_result_json(pdf_path, {"total_score_percent": 50, "student_name": "Test Student"})

            before = os.listdir(self._report_dir)
            result = finalize_report(
                row["id"],
                {"total_score_percent": 72, "student_name": "Test Student", "criteria_scores": []},
                reviewer_id=1,
            )
            after = os.listdir(self._report_dir)

            pdfs = [name for name in after if name.lower().endswith(".pdf")]
            self.assertEqual(pdfs.count(pdf_name), 1)
            self.assertEqual(len(pdfs), len([n for n in before if n.lower().endswith(".pdf")]))
            updated = get_report(row["id"])
            self.assertEqual(updated["status"], "published")
            self.assertEqual(updated["pdf_filename"], pdf_name)
            self.assertEqual(result["report"]["total_score_percent"], 72)


if __name__ == "__main__":
    unittest.main()
