"""Offline uploader tests using the SDK's actual vector-store polling helper."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

try:
    import httpx2 as httpx
except ImportError:
    import httpx
from openai import OpenAI

SCRIPT = Path(__file__).resolve().parents[1] / "upload_expert_calls_to_vector_store.py"
spec = importlib.util.spec_from_file_location("upload_transcripts_under_test", SCRIPT)
upload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload)


class UploadIndexingTests(unittest.TestCase):
    def run_upload(self, states, *, reject_upload=False, reject_poll=False, count=1):
        requests = []
        polls = []
        uploaded = []
        terminal_states = list(states)

        def handler(request):
            route = request.url.path
            requests.append((request.method, route))
            if request.method == "POST" and route == "/v1/vector_stores":
                return httpx.Response(
                    200, json={"id": "vs_synthetic", "object": "vector_store", "created_at": 0}
                )
            if request.method == "POST" and route == "/v1/files":
                if reject_upload:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": "Synthetic upload failure",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                self.assertIn(b"Synthetic transcript.", request.content)
                identity = f"file-{len(uploaded)}"
                uploaded.append(identity)
                return httpx.Response(
                    200,
                    json={
                        "id": identity,
                        "object": "file",
                        "bytes": 21,
                        "created_at": 0,
                        "filename": "synthetic.txt",
                        "purpose": "assistants",
                    },
                )
            if request.method == "POST" and route == "/v1/vector_stores/vs_synthetic/files":
                identity = json.loads(request.content)["file_id"]
                return httpx.Response(
                    200,
                    json={
                        "id": identity,
                        "object": "vector_store.file",
                        "vector_store_id": "vs_synthetic",
                        "created_at": 0,
                        "usage_bytes": 0,
                        "status": "in_progress",
                    },
                )
            if request.method == "GET" and route.startswith(
                "/v1/vector_stores/vs_synthetic/files/"
            ):
                if reject_poll:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": "Synthetic polling failure",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                index = int(route.rsplit("file-", 1)[1])
                previous = sum(item == index for item in polls)
                polls.append(index)
                state = "in_progress" if previous == 0 else terminal_states[index]
                return httpx.Response(
                    200,
                    headers={"openai-poll-after-ms": "0"},
                    json={
                        "id": f"file-{index}",
                        "object": "vector_store.file",
                        "vector_store_id": "vs_synthetic",
                        "created_at": 0,
                        "usage_bytes": 21,
                        "status": state,
                        "last_error": {
                            "code": "unsupported_file",
                            "message": "Synthetic indexing failure",
                        }
                        if state == "failed"
                        else None,
                    },
                )
            raise AssertionError(f"Unexpected synthetic request: {request.method} {route}")

        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            for index in range(count):
                (data / f"{index}.txt").write_text("Synthetic transcript.", encoding="utf-8")
            with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as transport:
                client = OpenAI(
                    api_key="synthetic-test-key",
                    base_url="https://example.invalid/v1",
                    http_client=transport,
                    max_retries=0,
                )
                with (
                    patch.object(upload, "OpenAI", return_value=client),
                    patch.object(
                        upload,
                        "parse_args",
                        return_value=SimpleNamespace(name="Synthetic store", data_dir=data),
                    ),
                    patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-test-key"}),
                ):
                    output, errors = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        result = upload.main()
        return result, output.getvalue(), errors.getvalue(), polls, requests

    def test_success_waits_for_each_file_to_finish_indexing(self):
        result, output, errors, polls, _ = self.run_upload(["completed", "completed"], count=2)
        self.assertEqual(result, 0)
        self.assertEqual(polls, [0, 0, 1, 1])
        self.assertIn("populated successfully", output)
        self.assertIn("VECTOR_STORE_ID=vs_synthetic", output)
        self.assertEqual(errors, "")

    def test_failed_indexing_is_not_announced_as_success(self):
        result, output, errors, polls, _ = self.run_upload(["failed"])
        self.assertEqual(result, 1)
        self.assertEqual(polls, [0, 0])
        self.assertNotIn("populated successfully", output)
        self.assertIn("failed", errors.lower())

    def test_cancelled_indexing_is_not_announced_as_success(self):
        result, output, errors, _, _ = self.run_upload(["cancelled"])
        self.assertEqual(result, 1)
        self.assertNotIn("populated successfully", output)
        self.assertIn("cancelled", errors.lower())

    def test_failure_stops_before_uploading_later_transcripts(self):
        result, _, _, _, requests = self.run_upload(["failed", "completed"], count=2)
        self.assertEqual(result, 1)
        self.assertEqual(requests.count(("POST", "/v1/files")), 1)

    def test_poll_request_error_returns_failure(self):
        result, output, errors, _, _ = self.run_upload(["completed"], reject_poll=True)
        self.assertEqual(result, 1)
        self.assertNotIn("populated successfully", output)
        self.assertIn("Synthetic polling failure", errors)

    def test_existing_upload_failure_still_returns_failure(self):
        result, output, errors, polls, _ = self.run_upload(["completed"], reject_upload=True)
        self.assertEqual(result, 1)
        self.assertEqual(polls, [])
        self.assertNotIn("populated successfully", output)
        self.assertIn("Synthetic upload failure", errors)


if __name__ == "__main__":
    unittest.main()
