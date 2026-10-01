from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.helpers import _build_row, _load_trend_rows


class TrendRowsTests(unittest.TestCase):
    def test_json_zero_metrics_match_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "trends.json"
            json_path.write_text(
                json.dumps([{"query": "example", "search_index": 0, "branding_mix": 0.0}]),
                encoding="utf-8",
            )
            csv_path = root / "trends.csv"
            csv_path.write_text(
                "query,search_index,branding_mix\nexample,0,0.0\n", encoding="utf-8"
            )
            for path in (json_path, csv_path):
                with self.subTest(format=path.suffix):
                    row = _load_trend_rows(path)[0]
                    self.assertEqual(row["search_index"], 0)
                    self.assertEqual(row["branding_mix"], 0.0)

    def test_zero_takes_precedence_over_later_aliases(self) -> None:
        for raw in (
            {"search_index": 0, "index": 8, "branding_mix": 0, "mix_share": 0.5},
            {"index": 0, "score": 8, "mix_share": 0, "metric": 0.5},
            {"score": 0, "implied_unit_sales_impact": 0, "metric": 0.5},
        ):
            with self.subTest(raw=raw):
                row = _build_row(raw, "trends.json", "json")
                self.assertEqual(row["search_index"], 0)
                self.assertEqual(row["branding_mix"], 0.0)

    def test_missing_or_empty_metrics_still_fall_back(self) -> None:
        row = _build_row(
            {"search_index": None, "index": "", "score": 8,
             "branding_mix": "", "mix_share": None, "metric": 0.5},
            "trends.json", "json",
        )
        self.assertEqual(row["search_index"], 8)
        self.assertEqual(row["branding_mix"], 0.5)
        empty = _build_row({}, "trends.json", "json")
        self.assertIsNone(empty["search_index"])
        self.assertIsNone(empty["branding_mix"])


if __name__ == "__main__":
    unittest.main()
