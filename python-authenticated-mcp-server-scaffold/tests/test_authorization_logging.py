from __future__ import annotations

import os
import unittest

from fastapi import Request
from starlette.responses import Response

os.environ.setdefault("AUTH0_ISSUER", "https://example.auth0.com/")
os.environ.setdefault("JWT_AUDIENCES", "https://example.com/mcp")
os.environ.setdefault("RESOURCE_SERVER_URL", "http://localhost:8788/")

from server.app import log_authorization_header


class AuthorizationLoggingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request(*, authorization: str | None = None) -> Request:
        headers: list[tuple[bytes, bytes]] = []
        if authorization is not None:
            headers.append((b"authorization", authorization.encode("utf-8")))

        return Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": headers,
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "root_path": "",
            }
        )

    @staticmethod
    async def _call_next(_: Request) -> Response:
        return Response(status_code=200)

    async def test_authorization_header_value_is_not_logged(self) -> None:
        secret = "Bearer regression-secret-token"
        request = self._request(authorization=secret)

        with self.assertLogs("mcp.server.auth", level="INFO") as captured:
            response = await log_authorization_header(request, self._call_next)

        output = "\n".join(captured.output)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, output)
        self.assertIn("Authorization header present on request to /mcp", output)

    async def test_missing_authorization_header_keeps_diagnostic(self) -> None:
        request = self._request()

        with self.assertLogs("mcp.server.auth", level="INFO") as captured:
            await log_authorization_header(request, self._call_next)

        self.assertIn("No Authorization header on request to /mcp", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
