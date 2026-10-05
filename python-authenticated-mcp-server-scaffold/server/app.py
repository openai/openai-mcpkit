# server.py
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any
from pydantic import AnyHttpUrl

from fastapi import Request
from dotenv import load_dotenv
from openai import OpenAI

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from server.token_verifiers import JWTVerifier

import logging

from server.helpers import (
    _available_trend_files,
    _collect_text_from_content,
    _load_trend_rows,
    _parse_iso_date,
)

load_dotenv()

LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
_resolved_level = logging.getLevelName(LOG_LEVEL_NAME)
if isinstance(_resolved_level, str):
    LOG_LEVEL = logging.INFO
else:
    LOG_LEVEL = _resolved_level

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")

if isinstance(_resolved_level, str):
    logging.warning("Unrecognized LOG_LEVEL '%s', defaulting to INFO", LOG_LEVEL_NAME)


# ---- Configuration ----
PORT = int(os.getenv("PORT", "8788"))
logging.info(f"PORT: {PORT}")
RESOURCE_SERVER_URL = os.getenv("RESOURCE_SERVER_URL", f"http://localhost:{PORT}/")
logging.info(f"RESOURCE_SERVER_URL: {RESOURCE_SERVER_URL}")
AUTH_ISSUER = os.getenv("AUTH0_ISSUER")
logging.info(f"AUTH_ISSUER: {AUTH_ISSUER}")
REQUIRED_SCOPES = ["openid", "profile", "email"]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID")
TREND_DATA_DIR = Path(
    os.getenv(
        "TREND_DATA_DIR",
        Path(__file__).resolve().parent.parent.parent / "synthetic_financial_data" / "web_search_trends",
    )
).resolve()

JWKS_URI = f"{AUTH_ISSUER.rstrip('/')}/.well-known/jwks.json"

# ---- FastMCP server (no FastAPI/Uvicorn needed) ----
mcp = FastMCP(
    name="python-authenticated-mcp",
    instructions=(
        "Authenticated MCP server in Python. Provides `search`/`fetch` over a travel-industry "
        "expert call transcript vector store, plus `trend_insights` on CSV/TSV/JSON web-search "
        "trend extracts."
    ),
    token_verifier=JWTVerifier(
        jwks_uri=JWKS_URI,
        issuer=AUTH_ISSUER,
    ),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(AUTH_ISSUER),
        resource_server_url=AnyHttpUrl(RESOURCE_SERVER_URL),
        required_scopes=REQUIRED_SCOPES,
    )
)

# # Mount Streamable HTTP at the root (i.e., the MCP endpoint is "/")
# mcp.streamable_http_path = "/"

logging.getLogger("mcp.server").setLevel(LOG_LEVEL)
logging.getLogger("mcp.server.auth").setLevel(LOG_LEVEL)

# ---- Tools ----
def _openai_client() -> OpenAI:
    # If OPENAI_API_KEY is unset, OpenAI() will use env/ambient config if available.
    return OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else OpenAI()


@mcp.tool()
async def search(query: str) -> dict[str, Any]:
    """
    Search the OpenAI Vector Store of travel-industry expert call transcripts.
    Returns: {"results": [{"id","title","text","url"}...]}
    """
    results: list[dict[str, str]] = []
    if not query or not query.strip() or not VECTOR_STORE_ID:
        return {"results": results}

    client = _openai_client()

    try:
        # Prefer the current signature
        resp = client.vector_stores.search(
            VECTOR_STORE_ID,
            {"query": query, "ranking_options": {"score_threshold": 0.5}, "rewrite_query": True},
        )
        data = getattr(resp, "data", None) or []

        logging.info(data)
    except Exception:
        # Fallback to keyword args in case of SDK shape differences
        try:
            resp = client.vector_stores.search(
                vector_store_id=VECTOR_STORE_ID,
                query=query,
                ranking_options={"score_threshold": 0.5},
                rewrite_query=True,
            )
            data = getattr(resp, "data", None) or []
        except Exception:
            data = []

    for i, item in enumerate(data):
        file_id = getattr(item, "file_id", None) or getattr(item, "id", None) or f"vs_{i}"
        filename = getattr(item, "filename", None) or f"Document {i+1}"
        content_list = getattr(item, "content", None) or []
        text_content = _collect_text_from_content(content_list)
        text_snippet = (text_content[:200] + "...") if len(text_content) > 200 else (text_content or "No content available")
        results.append(
            {
                "id": str(file_id),
                "title": str(filename),
                "text": text_snippet,
                "url": f"https://platform.openai.com/storage/files/{file_id}",
            }
        )

    return {"results": results}

@mcp.tool()
async def fetch(id: str) -> dict[str, Any]:
    """
    Fetch a full travel-industry expert call transcript by file ID from the OpenAI Vector Store.
    Returns: {"id","title","text","url","metadata":optional}
    """
    client = _openai_client()
    title = f"Document {id}"
    metadata: Any = None
    full_text = "No content available."

    if not id or not VECTOR_STORE_ID:
        logging.info("No id or vector store id")
        return {"id": id, "title": title, "text": full_text, "url": f"https://platform.openai.com/storage/files/{id}", "metadata": metadata}


    try:
        # Retrieve content chunks
        content_resp = client.vector_stores.files.content(
            file_id=id,
            vector_store_id=VECTOR_STORE_ID,
        )
        logging.info(content_resp)
        extracted_text = _collect_text_from_content(content_resp)
        if extracted_text:
            full_text = extracted_text

        # Optionally improve title/metadata
        try:
            file_info = client.vector_stores.files.retrieve(vector_store_id=VECTOR_STORE_ID, file_id=id)
            filename = getattr(file_info, "filename", None)
            if filename:
                title = filename
            attrs = getattr(file_info, "attributes", None)
            if attrs:
                metadata = attrs
        except Exception:
            pass

    except Exception:
        pass

    return {"id": id, "title": title, "text": full_text, "url": f"https://platform.openai.com/storage/files/{id}", "metadata": metadata}


@mcp.tool()
async def airfare_trend_insights(
    snapshot_date: str | None = None,
    route_contains: str | None = None,
    origin_airport: str | None = None,
    destination_airport: str | None = None,
    airline_contains: str | None = None,
    season_contains: str | None = None,
    notable_contains: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Surface US airfare route-level trend snapshots with airline-aware filters.

    Args map to dataset columns:
        snapshot_date: Optional exact or substring match on the YYYY-MM-DD snapshot (e.g. "2025-10-06").
        route_contains: Case-insensitive substring match over the route column ("JFK-LAX", "SFO").
        origin_airport: Exact 3-letter origin airport parsed from each route.
        destination_airport: Exact 3-letter destination airport parsed from each route.
        airline_contains: Substring match over the airline column ("Delta", "United").
        season_contains: Substring match over the season column.
        notable_contains: Substring match over the notable event/driver text.
        limit: Maximum number of rows returned (1-200).
    """

    available_paths = _available_trend_files(TREND_DATA_DIR)
    available_files = [path.name for path in available_paths]
    rows: list[dict[str, Any]] = []

    for path in available_paths:
        rows.extend(_load_trend_rows(path))

    if not rows:
        return {
            "rows": [],
            "available_files": available_files,
            "filters": {
                "snapshot_date": snapshot_date,
                "route_contains": route_contains,
                "origin_airport": origin_airport,
                "destination_airport": destination_airport,
                "airline_contains": airline_contains,
                "season_contains": season_contains,
                "notable_contains": notable_contains,
                "limit": limit,
            },
            "total_rows": 0,
            "matched_rows": 0,
            "rows_returned": 0,
            "trend_data_dir": str(TREND_DATA_DIR),
        }

    snapshot_filter = _parse_iso_date(snapshot_date)
    route_filter = (route_contains or "").lower()
    origin_filter = (origin_airport or "").lower()
    destination_filter = (destination_airport or "").lower()
    airline_filter = (airline_contains or "").lower()
    season_filter = (season_contains or "").lower()
    notable_filter = (notable_contains or "").lower()

    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        snapshot_value = _parse_iso_date(row.get("snapshot_date"))
        if snapshot_filter and snapshot_value != snapshot_filter:
            continue
        if route_filter and route_filter not in (row.get("route") or "").lower():
            continue
        if origin_filter and origin_filter != (row.get("origin_airport") or "").lower():
            continue
        if destination_filter and destination_filter != (row.get("destination_airport") or "").lower():
            continue
        if airline_filter and airline_filter not in (row.get("airline") or "").lower():
            continue
        if season_filter and season_filter not in (row.get("season") or "").lower():
            continue
        if notable_filter and notable_filter not in (row.get("notable_event") or "").lower():
            continue
        filtered_rows.append(row)

    filtered_rows.sort(
        key=lambda row: (
            _parse_iso_date(row.get("snapshot_date")) or date.min,
            row.get("route") or row.get("query") or "",
            row.get("airline") or "",
        ),
        reverse=True,
    )

    try:
        applied_limit = int(limit)
    except (TypeError, ValueError):
        applied_limit = 25
    applied_limit = max(1, min(applied_limit, 200))
    limited_rows = filtered_rows[:applied_limit]

    return {
        "rows": limited_rows,
        "available_files": available_files,
        "filters": {
            "snapshot_date": snapshot_date,
            "route_contains": route_contains,
            "origin_airport": origin_airport,
            "destination_airport": destination_airport,
            "airline_contains": airline_contains,
            "season_contains": season_contains,
            "notable_contains": notable_contains,
            "limit": applied_limit,
        },
        "matched_rows": len(filtered_rows),
        "rows_returned": len(limited_rows),
        "trend_data_dir": str(TREND_DATA_DIR),
    }

app = mcp.streamable_http_app()


@app.middleware("http")
async def log_authorization_header(request: Request, call_next):
    auth_header = request.headers.get("authorization")
    if auth_header:
        logging.getLogger("mcp.server.auth").info(
            "Authorization header present on request to %s", request.url.path
        )
    else:
        logging.getLogger("mcp.server.auth").info("No Authorization header on request to %s", request.url.path)
    response = await call_next(request)
    return response

if __name__ == "__main__":
    import os, uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8788")))
