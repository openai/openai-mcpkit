from __future__ import annotations

import csv
import json
import math
import re
from datetime import date, datetime
from itertools import chain
from pathlib import Path
from typing import Any

TIMESTAMP_PATTERN = re.compile(r"^\[(?P<timestamp>\d{2}:\d{2})\]\s*(?P<speaker>[^:]+):\s*(?P<content>.*)$")

SUPPORTED_TREND_SUFFIXES: dict[str, str] = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
}


def _collect_text_from_content(content_resp: Any) -> str:
    """Recursively collect text payloads from vector store content responses."""

    def _walk(node: Any) -> list[str]:
        if node is None:
            return []
        if isinstance(node, str):
            return [node]

        collected: list[str] = []
        text_value: Any = None
        data_value: Any = None

        if isinstance(node, dict):
            text_value = node.get("text")
            data_value = node.get("data")
        else:
            if hasattr(node, "text"):
                text_value = getattr(node, "text")
            if hasattr(node, "data"):
                data_value = getattr(node, "data")

        if text_value:
            if isinstance(text_value, (list, tuple)):
                collected.extend(str(item) for item in text_value if item)
            else:
                collected.append(str(text_value))

        if data_value:
            if isinstance(data_value, (list, tuple)):
                for item in data_value:
                    collected.extend(_walk(item))
            else:
                collected.extend(_walk(data_value))

        return collected

    return "\n".join(_walk(content_resp))


def _available_trend_files(
    trend_data_dir: Path, supported_suffixes: dict[str, str] | None = None
) -> list[Path]:
    if not trend_data_dir.exists():
        return []
    suffixes = supported_suffixes or SUPPORTED_TREND_SUFFIXES
    files: dict[str, Path] = {}
    for suffix in suffixes:
        for path in trend_data_dir.glob(f"*{suffix}"):
            files[path.name] = path
    return [files[name] for name in sorted(files)]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value) if value is not None and value != "" else None
        return number if number is not None and math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _build_row(raw: dict[str, Any], source_file: str, source_format: str) -> dict[str, Any]:
    query = (raw.get("query") or raw.get("keyword") or raw.get("term") or raw.get("search_term") or "").strip()
    snapshot_date_value = (raw.get("snapshot_date") or "").strip()
    date_value = (
        snapshot_date_value
        or (raw.get("date") or raw.get("week") or raw.get("week_id") or raw.get("period") or "")
    ).strip()
    region_val = (raw.get("region") or raw.get("region_name") or raw.get("geo") or "").strip()
    search_index = _coerce_int(raw.get("search_index") or raw.get("index") or raw.get("score"))
    branding_mix = _coerce_float(
        raw.get("branding_mix")
        or raw.get("mix_share")
        or raw.get("implied_unit_sales_impact")
        or raw.get("metric")
    )

    notable_event = (
        raw.get("notable_driver")
        or raw.get("notable_event")
        or raw.get("notes")
        or raw.get("channel_checks")
        or raw.get("processing_notes")
        or ""
    ).strip()

    route_value = (raw.get("route") or "").strip()
    airline_value = (raw.get("airline") or "").strip()
    season_value = (raw.get("season") or "").strip()

    if not query:
        if route_value or airline_value:
            query = " ".join(part for part in (route_value, airline_value, season_value) if part).strip()
        if not query:
            query = (raw.get("provider") or raw.get("category") or raw.get("topic") or "").strip()

    row: dict[str, Any] = {
        "query": query,
        "date": date_value,
        "snapshot_date": snapshot_date_value or date_value,
        "region": region_val,
        "search_index": search_index,
        "branding_mix": branding_mix,
        "notable_event": notable_event,
        "source_file": source_file,
        "source_format": source_format,
    }

    for extra_key in ("linked_tickers", "provider", "collection_window", "query_count", "week_id"):
        if extra_key in raw and raw[extra_key] not in (None, ""):
            row[extra_key] = raw[extra_key]

    if route_value:
        row["route"] = route_value
        if "-" in route_value:
            origin, destination = route_value.split("-", 1)
            origin = origin.strip()
            destination = destination.strip()
            if origin:
                row["origin_airport"] = origin
            if destination:
                row["destination_airport"] = destination

    if airline_value:
        row["airline"] = airline_value

    if season_value:
        row["season"] = season_value

    avg_fare = _coerce_float(raw.get("avg_fare_usd"))
    if avg_fare is not None:
        row["avg_fare_usd"] = avg_fare

    premium_fare = _coerce_float(raw.get("premium_fare_usd"))
    if premium_fare is not None:
        row["premium_fare_usd"] = premium_fare

    yoy_change = _coerce_float(raw.get("fare_yoy_pct"))
    if yoy_change is not None:
        row["fare_yoy_pct"] = yoy_change

    advance_purchase = _coerce_int(raw.get("advance_purchase_days"))
    if advance_purchase is not None:
        row["advance_purchase_days"] = advance_purchase

    load_factor = _coerce_float(raw.get("load_factor_pct"))
    if load_factor is not None:
        row["load_factor_pct"] = load_factor

    ancillary_mix = _coerce_float(raw.get("ancillary_revenue_pct"))
    if ancillary_mix is not None:
        row["ancillary_revenue_pct"] = ancillary_mix

    notable_driver = (raw.get("notable_driver") or "").strip()
    if notable_driver:
        row["notable_driver"] = notable_driver
        row["notable_event"] = notable_driver

    if not row["query"]:
        fallback = " ".join(part for part in (row.get("airline"), row.get("route"), row.get("season")) if part)
        row["query"] = fallback or source_file

    return row


def _load_tabular_rows(file_path: Path, delimiter: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not file_path.exists():
        return rows

    with file_path.open(newline="", encoding="utf-8") as csvfile:
        data_lines: list[str] = []
        header_line: str | None = None
        for raw_line in csvfile:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                comment_content = stripped.lstrip("#").strip()
                if header_line is None and delimiter in comment_content:
                    header_line = comment_content + "\n"
                continue
            data_lines.append(raw_line)

        if not data_lines:
            return rows

        source_format = "tsv" if delimiter == "\t" else "csv"
        iterator = chain([header_line] if header_line else [], data_lines)
        reader = csv.DictReader(iterator, delimiter=delimiter)
        for line in reader:
            rows.append(_build_row(line, file_path.name, source_format))
    return rows


def _load_json_rows(file_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not file_path.exists():
        return rows
    try:
        with file_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return rows

    def _ingest(raw: dict[str, Any], defaults: dict[str, Any] | None = None) -> None:
        merged = dict(defaults or {})
        merged.update(raw)
        rows.append(_build_row(merged, file_path.name, "json"))

    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                _ingest(entry)
        return rows

    if isinstance(payload, dict):
        regions = payload.get("regions")
        if isinstance(regions, dict):
            week_value = payload.get("week")
            for region_name, region_payload in regions.items():
                defaults = {
                    "region": region_name,
                    "week": week_value,
                    "channel_checks": region_payload.get("channel_checks"),
                }
                top_queries = region_payload.get("top_queries")
                if isinstance(top_queries, list):
                    for entry in top_queries:
                        if isinstance(entry, dict):
                            _ingest(entry, defaults)
                else:
                    _ingest(region_payload, defaults)
        else:
            rows.append(_build_row(payload, file_path.name, "json"))

    return rows


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    if len(value) == 8 and value[4:6].upper() == "-W":
        try:
            year = int(value[:4])
            week = int(value[6:])
            return date.fromisocalendar(year, week, 1)
        except ValueError:
            return None
    return None


def _load_trend_rows(file_path: Path) -> list[dict[str, Any]]:
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return _load_tabular_rows(file_path, ",")
    if suffix == ".tsv":
        return _load_tabular_rows(file_path, "\t")
    if suffix == ".json":
        return _load_json_rows(file_path)
    return []


__all__ = [
    "_available_trend_files",
    "_available_expert_call_files",
    "_parse_expert_call_file",
    "_coerce_int",
    "_coerce_float",
    "_load_trend_rows",
    "_parse_iso_date",
    "_collect_text_from_content",
    "SUPPORTED_TREND_SUFFIXES",
]
