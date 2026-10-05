"""Offline tests for quoted trend records and comment preprocessing."""
import csv
import io
from pathlib import Path
import tempfile
import unittest

from server.helpers import _load_tabular_rows


class QuotedTrendTests(unittest.TestCase):
    def test_quoted_fields_preserve_comment_lines_and_blanks(self):
        for delimiter in (",", "\t"):
            for newline in ("\n", "\r\n"):
                for commented_header in (False, True):
                    with self.subTest(delimiter=delimiter, newline=newline, header=commented_header):
                        note = f'first{newline}#literal{delimiter}text{newline}{newline}last "quoted"'
                        source = io.StringIO(newline="")
                        writer = csv.writer(source, delimiter=delimiter, lineterminator=newline)
                        writer.writerow(["query", "date", "notes"])
                        writer.writerow(["synthetic", "2026-10-01", note])
                        writer.writerow(["control", "2026-10-02", "ordinary note"])
                        text = source.getvalue()
                        if commented_header:
                            text = "# " + text
                        text = "# metadata\n\n" + text + '# ignored "comment\n'
                        with tempfile.TemporaryDirectory() as directory:
                            path = Path(directory)/("fixture.csv" if delimiter == "," else "fixture.tsv")
                            path.write_text(text, encoding="utf-8", newline="")
                            rows = _load_tabular_rows(path, delimiter)
                        self.assertEqual(len(rows), 2)
                        self.assertEqual(rows[0]["notable_event"], note)
                        self.assertEqual(rows[1]["query"], "control")
                        self.assertEqual(rows[1]["notable_event"], "ordinary note")

    def test_simple_files_and_comment_only_files_keep_existing_behavior(self):
        for contents, count in [("# metadata\n\n", 0), ("query,notes\nplain,normal\n", 1)]:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"fixture.csv"
                path.write_text(contents, encoding="utf-8")
                self.assertEqual(len(_load_tabular_rows(path, ",")), count)


if __name__ == "__main__":
    unittest.main()
