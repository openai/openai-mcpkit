"""Offline coercion and ingestion regressions for non-finite numeric values."""

import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.helpers import _coerce_float, _coerce_int, _load_trend_rows


@pytest.mark.parametrize(
    "value",
    ["NaN", "nan", "Infinity", "-inf", "1e999", float("inf"), float("nan"), Decimal("Infinity")],
)
def test_nonfinite_float_values_are_missing(value):
    assert _coerce_float(value) is None


@pytest.mark.parametrize(
    "value", [float("inf"), float("-inf"), float("nan"), Decimal("Infinity"), Decimal("NaN")]
)
def test_nonfinite_integer_values_are_missing(value):
    assert _coerce_int(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("", None), ("missing", None), (0, 0), ("0", 0), ("-2.5", -2.5), ("1e2", 100)],
)
def test_finite_float_and_invalid_text_behavior_is_unchanged(value, expected):
    assert _coerce_float(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("", None), ("missing", None), (0, 0), ("0", 0), (-2.5, -2), ("100", 100)],
)
def test_integer_coercion_controls(value, expected):
    assert _coerce_int(value) == expected


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".json"])
def test_nonfinite_fares_do_not_poison_other_rows_or_json(tmp_path, suffix):
    entries = [
        {
            "query": "Synthetic invalid",
            "snapshot_date": "2026-10-06",
            "avg_fare_usd": "NaN",
            "load_factor_pct": "Infinity",
            "fare_yoy_pct": "-1e999",
        },
        {
            "query": "Synthetic valid",
            "snapshot_date": "2026-10-06",
            "avg_fare_usd": "25.5",
            "load_factor_pct": "75",
            "fare_yoy_pct": "-2.5",
        },
    ]
    path = tmp_path / ("synthetic" + suffix)
    if suffix == ".json":
        path.write_text(json.dumps(entries), encoding="utf-8")
    else:
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output, fieldnames=list(entries[0]), delimiter="\t" if suffix == ".tsv" else ","
            )
            writer.writeheader()
            writer.writerows(entries)
    rows = _load_trend_rows(path)
    assert len(rows) == 2
    assert (
        "avg_fare_usd" not in rows[0]
        and "load_factor_pct" not in rows[0]
        and "fare_yoy_pct" not in rows[0]
    )
    assert rows[1]["avg_fare_usd"] == 25.5
    assert rows[1]["load_factor_pct"] == 75
    assert rows[1]["fare_yoy_pct"] == -2.5
    assert json.loads(json.dumps(rows, allow_nan=False)) == rows


def test_overflowing_integer_json_metric_does_not_abort_ingestion(tmp_path):
    path = tmp_path / "synthetic.json"
    # Valid JSON numeric syntax can overflow a binary64 decoder.
    path.write_text(
        '[{"query":"Synthetic","search_index":1e999,"advance_purchase_days":1e999}]',
        encoding="utf-8",
    )
    rows = _load_trend_rows(path)
    assert len(rows) == 1 and rows[0]["query"] == "Synthetic"
    assert rows[0]["search_index"] is None
    assert "advance_purchase_days" not in rows[0]
    json.dumps(rows, allow_nan=False)
