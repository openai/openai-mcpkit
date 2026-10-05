from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openai.types.vector_store_search_response import Content, VectorStoreSearchResponse

os.environ.setdefault("AUTH0_ISSUER", "https://example.auth0.com/")
os.environ.setdefault("JWT_AUDIENCES", "https://example.com/mcp")
os.environ.setdefault("RESOURCE_SERVER_URL", "http://localhost:8788/")

from server import app
from server.helpers import _collect_text_from_content


class SearchContentTests(unittest.IsolatedAsyncioTestCase):
    async def _search(self, item):
        client = SimpleNamespace(
            vector_stores=SimpleNamespace(search=Mock(return_value=SimpleNamespace(data=[item])))
        )
        with (
            patch.object(app, "VECTOR_STORE_ID", "vs_synthetic"),
            patch.object(app, "_openai_client", return_value=client),
        ):
            result = await app.search("synthetic question")
        return result["results"][0]

    async def test_search_preserves_typed_sdk_content(self):
        item = VectorStoreSearchResponse(
            attributes={},
            content=[
                Content(type="text", text="First excerpt"),
                Content(type="text", text="Second excerpt"),
            ],
            file_id="file-synthetic",
            filename="synthetic.txt",
            score=0.9,
        )
        result = await self._search(item)
        self.assertEqual(result["text"], "First excerpt\nSecond excerpt")
        self.assertEqual(result["id"], "file-synthetic")
        self.assertEqual(result["title"], "synthetic.txt")

    async def test_search_truncates_typed_text_at_existing_limit(self):
        item = SimpleNamespace(content=[Content(type="text", text="é" * 250)])
        result = await self._search(item)
        self.assertEqual(result["text"], "é" * 200 + "...")

    async def test_search_preserves_dictionary_and_string_content(self):
        for content in ([{"text": "dictionary excerpt"}], ["string excerpt"]):
            with self.subTest(content=content):
                result = await self._search(SimpleNamespace(content=content))
                self.assertIn("excerpt", result["text"])

    async def test_search_empty_content_keeps_fallback(self):
        for content in ([], None, [{"text": ""}]):
            with self.subTest(content=content):
                result = await self._search(SimpleNamespace(content=content))
                self.assertEqual(result["text"], "No content available")

    def test_collector_preserves_order_across_nested_sequences(self):
        value = [Content(type="text", text="one"), ({"text": "two"}, "three")]
        self.assertEqual(_collect_text_from_content(value), "one\ntwo\nthree")

    def test_collector_keeps_paginated_fetch_content(self):
        value = SimpleNamespace(data=[Content(type="text", text="one"), {"text": "two"}])
        self.assertEqual(_collect_text_from_content(value), "one\ntwo")


if __name__ == "__main__":
    unittest.main()
