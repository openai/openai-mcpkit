#!/usr/bin/env python3
"""
Create an OpenAI vector store and upload the synthetic expert call transcripts.

Usage:
    python scripts/upload_expert_calls_to_vector_store.py [--name NAME] [--data-dir PATH]

The script prints the created vector store ID so it can be copied into the
`VECTOR_STORE_ID` environment variable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from openai import OpenAI, OpenAIError
except ImportError:  # pragma: no cover - fallback for older SDK versions
    from openai import OpenAI  # type: ignore
    from openai.error import OpenAIError  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "synthetic_financial_data" / "expert_calls"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload synthetic expert call transcripts to an OpenAI vector store."
    )
    parser.add_argument(
        "--name",
        default="Synthetic Expert Call Transcripts",
        help="Name to assign to the created vector store.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing expert call transcript files.",
    )
    return parser.parse_args()


def gather_transcript_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        raise FileNotFoundError(f"Transcript directory does not exist: {data_dir}")

    transcript_files = sorted(
        [path for path in data_dir.glob("**/*") if path.is_file()],
        key=lambda path: path.name,
    )

    if not transcript_files:
        raise FileNotFoundError(
            f"No transcript files found in directory: {data_dir}"
        )

    return transcript_files


def upload_transcripts(vector_store_id: str, transcript_paths: list[Path]) -> None:
    client = OpenAI()

    for path in transcript_paths:
        with path.open("rb") as file_handle:
            uploaded = client.files.create(
                file=(path.name, file_handle),
                purpose="assistants",
            )
        indexed = client.vector_stores.files.create_and_poll(
            vector_store_id=vector_store_id,
            file_id=uploaded.id,
        )
        if indexed.status != "completed":
            raise OpenAIError(
                f"Transcript {path.name} was not indexed ({indexed.status})."
            )


def main() -> int:
    args = parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Please export your API key before running this script.",
            file=sys.stderr,
        )
        return 1

    try:
        transcript_paths = gather_transcript_files(args.data_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    client = OpenAI()

    try:
        vector_store = client.vector_stores.create(name=args.name)
    except OpenAIError as exc:
        print(f"Failed to create vector store: {exc}", file=sys.stderr)
        return 1

    try:
        upload_transcripts(vector_store.id, transcript_paths)
    except OpenAIError as exc:
        print(f"Failed to upload transcripts: {exc}", file=sys.stderr)
        return 1

    print("Vector store created and populated successfully.")
    print(f"VECTOR_STORE_ID={vector_store.id}")
    print("Set this value in your environment, for example:")
    print(f"export VECTOR_STORE_ID={vector_store.id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
