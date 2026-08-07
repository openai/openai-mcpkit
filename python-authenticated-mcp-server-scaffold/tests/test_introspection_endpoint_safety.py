from __future__ import annotations

import unittest

from server.token_verifiers import IntrospectionTokenVerifier


class IntrospectionEndpointSafetyTests(unittest.TestCase):
    @staticmethod
    def _is_safe(endpoint: str) -> bool:
        verifier = object.__new__(IntrospectionTokenVerifier)
        verifier.introspection_endpoint = endpoint
        return verifier._is_endpoint_safe()

    def test_https_endpoint_is_allowed(self) -> None:
        self.assertTrue(self._is_safe("https://idp.example.com/oauth/introspect"))

    def test_exact_http_loopback_hosts_are_allowed(self) -> None:
        self.assertTrue(self._is_safe("http://localhost/introspect"))
        self.assertTrue(self._is_safe("http://localhost:8080/introspect"))
        self.assertTrue(self._is_safe("http://127.0.0.1/introspect"))
        self.assertTrue(self._is_safe("http://127.0.0.1:8080/introspect"))

    def test_prefixed_non_loopback_hosts_are_rejected(self) -> None:
        self.assertFalse(self._is_safe("http://localhost.attacker.example/introspect"))
        self.assertFalse(self._is_safe("http://localhostile.example/introspect"))
        self.assertFalse(self._is_safe("http://127.0.0.1.attacker.example/introspect"))

    def test_userinfo_does_not_turn_remote_host_into_loopback(self) -> None:
        self.assertFalse(self._is_safe("http://localhost@attacker.example/introspect"))

    def test_other_cleartext_or_malformed_endpoints_are_rejected(self) -> None:
        self.assertFalse(self._is_safe("http://idp.example.com/introspect"))
        self.assertFalse(self._is_safe("ftp://localhost/introspect"))
        self.assertFalse(self._is_safe("https://"))
        self.assertFalse(self._is_safe("http://[::1"))


if __name__ == "__main__":
    unittest.main()
