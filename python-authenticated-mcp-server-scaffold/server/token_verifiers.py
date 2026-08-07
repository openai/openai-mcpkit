from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url

logger = logging.getLogger(__name__)


class SimpleTokenVerifier(TokenVerifier):
    """Development helper that blindly accepts any token."""

    def __init__(self, required_scopes: Iterable[str]):
        self.required_scopes: list[str] = list(required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        logger.info("SimpleTokenVerifier received token: %s", token)
        # TODO: Do not use in production—replace with a real verifier.
        return AccessToken(
            token=token or "dev_token",
            client_id="dev_client",
            subject="dev",
            scopes=self.required_scopes or [],
            claims={"debug": True},
        )


class IntrospectionTokenVerifier(TokenVerifier):
    """Verify OAuth 2.0 access tokens using RFC 7662 token introspection."""

    def __init__(
        self,
        introspection_endpoint: str,
        server_url: str,
        validate_resource: bool = False,
        *,
        auth_headers: dict[str, str] | None = None,
        auth_data: dict[str, str] | None = None,
    ):
        """
        Args:
            introspection_endpoint: URL of the authorization server's introspection endpoint.
            server_url: Base URL of this MCP server (used for resource validation).
            validate_resource: Enforce RFC 8707 resource binding checks when True.
            auth_headers: Optional static headers (e.g., Basic auth) to send with requests.
            auth_data: Optional extra form fields (e.g., client_id/secret) to include.
        """
        self.introspection_endpoint = introspection_endpoint
        self.server_url = server_url
        self.validate_resource = validate_resource
        self.resource_url = resource_url_from_server_url(server_url)
        self.auth_headers = auth_headers or {}
        self.auth_data = auth_data or {}

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token via the configured introspection endpoint."""
        import httpx

        if not self._is_endpoint_safe():
            logger.warning("Rejecting introspection endpoint with unsafe scheme: %s", self.introspection_endpoint)
            return None

        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        payload: dict[str, str] = {"token": token}
        payload.update(self.auth_data)

        async with httpx.AsyncClient(timeout=timeout, limits=limits, verify=True) as client:
            try:
                response = await client.post(
                    self.introspection_endpoint,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded", **self.auth_headers},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Token introspection request failed: %s", exc)
                return None

        if response.status_code != 200:
            logger.debug("Token introspection returned status %s", response.status_code)
            return None

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Token introspection response was not valid JSON: %s", exc)
            return None

        if not data.get("active", False):
            return None

        if self.validate_resource and not self._validate_resource(data):
            logger.warning("Token resource validation failed. Expected: %s", self.resource_url)
            return None

        scopes = self._parse_scopes(data.get("scope"))

        return AccessToken(
            token=token,
            client_id=data.get("client_id", "unknown"),
            scopes=scopes,
            expires_at=data.get("exp"),
            resource=data.get("aud"),
            claims=data,
        )

    def _is_endpoint_safe(self) -> bool:
        try:
            parsed = urlsplit(self.introspection_endpoint)
            hostname = parsed.hostname
        except ValueError:
            return False

        if parsed.scheme.lower() == "https":
            return hostname is not None

        return parsed.scheme.lower() == "http" and hostname in {"localhost", "127.0.0.1"}

    @staticmethod
    def _parse_scopes(scope_value: str | Sequence[str] | None) -> list[str]:
        if isinstance(scope_value, str):
            return scope_value.split()
        if isinstance(scope_value, (list, tuple)):
            return [str(item) for item in scope_value]
        return []

    def _validate_resource(self, token_data: dict[str, Any]) -> bool:
        if not self.server_url or not self.resource_url:
            return False

        audience: Any = token_data.get("aud")
        if isinstance(audience, list):
            return any(self._is_valid_resource(item) for item in audience)
        if isinstance(audience, str):
            return self._is_valid_resource(audience)
        return False

    def _is_valid_resource(self, resource: str) -> bool:
        if not self.resource_url:
            return False
        return check_resource_allowed(requested_resource=self.resource_url, configured_resource=resource)

class JWTVerifier(TokenVerifier):
    """
    Minimal JWT verifier that fetches signing keys from a JWKS endpoint and validates
    signed (JWS) tokens. This does not support encrypted (JWE) tokens.
    """

    def __init__(
        self,
        jwks_uri: str,
        *,
        issuer: str | None = None,
    ):
        if not jwks_uri:
            raise ValueError("jwks_uri is required for JWTVerifier.")
        self._jwks_uri = jwks_uri
        self._issuer = issuer

        audiences_raw = os.getenv("JWT_AUDIENCES")
        if not audiences_raw:
            logger.warning("JWTVerifier initialized without JWT_AUDIENCES; audience validation may fail.")
            self._audiences = ()
        else:
            self._audiences = tuple(item.strip() for item in audiences_raw.split(",") if item.strip())
            logger.debug("JWTVerifier configured with audiences: %s", self._audiences)
        self._jwk_client = PyJWKClient(jwks_uri)

        fallback_scopes_raw = os.getenv("REQUIRED_SCOPES", "")
        self._fallback_scopes = tuple(
            scope.strip() for scope in fallback_scopes_raw.split(",") if scope.strip()
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        logger.debug(
            "JWTVerifier starting verification; issuer=%s, expected_audiences=%s",
            self._issuer or "<unspecified>",
            self._audiences,
        )

        if token.count(".") != 2:
            segment_count = token.count(".") + 1 if token else 0
            logger.warning(
                "JWTVerifier expected a signed JWT with 3 segments; received %s segments.",
                segment_count,
            )
            return None

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            logger.warning("JWTVerifier failed to parse JWT header: %s", exc)
            return None

        logger.debug(
            "JWTVerifier parsed header; kid=%s alg=%s typ=%s",
            header.get("kid", "<unspecified>"),
            header.get("alg", "<unspecified>"),
            header.get("typ", "<unspecified>"),
        )

        logger.debug(
            "JWTVerifier fetching signing key from %s for kid=%s",
            self._jwks_uri,
            header.get("kid", "<unspecified>"),
        )
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            key = signing_key.key
        except Exception as exc:  # noqa: BLE001
            logger.warning("JWTVerifier failed to resolve signing key from %s: %s", self._jwks_uri, exc)
            return None

        decode_kwargs: dict[str, Any] = {
            "key": key,
            "algorithms": ["RS256"],
            "audience": list(self._audiences),
            "options": {
                "require": ["exp", "iat"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": bool(self._issuer),
            },
        }
        if self._issuer:
            decode_kwargs["issuer"] = self._issuer

        logger.debug(
            "JWTVerifier decoding JWT; issuer_validation=%s audience_validation=%s",
            bool(self._issuer),
            bool(self._audiences),
        )
        try:
            claims = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError:
            logger.info("JWTVerifier rejected token: signature expired.")
            return None
        except jwt.PyJWTError as exc:
            logger.warning(
                "JWTVerifier failed to verify JWT with issuer=%s audiences=%s: %s",
                self._issuer or "<unspecified>",
                self._audiences,
                exc,
            )
            return None

        subject = claims.get("sub") or claims.get("client_id")

        resource_aud = claims.get("aud")
        if isinstance(resource_aud, list):
            resource = next((aud for aud in resource_aud if aud in self._audiences), resource_aud[0] if resource_aud else None)
        else:
            resource = resource_aud
        if resource is None:
            resource = self._audiences[0]

        scopes = self._extract_scopes(claims)

        logger.debug(f"JWTVerifier scopes: {scopes}")
       
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or "unknown_client"),
            subject=subject,
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=resource,
            claims=claims,
        )

    @staticmethod
    def _extract_scopes(claims: dict[str, Any]) -> list[str]:
        scope_value = claims.get("scope") or claims.get("scp")
        if isinstance(scope_value, str):
            return scope_value.split()
        if isinstance(scope_value, (list, tuple)):
            return [str(item) for item in scope_value]
        return []


__all__ = ["SimpleTokenVerifier", "IntrospectionTokenVerifier", "OpenIDTokenVerifier", "JWTVerifier"]
