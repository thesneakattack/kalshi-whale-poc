"""Tests for services/kalshi/transport.py — vendor SDK-client construction
glue behind the integration boundary (Kalshi Integration Phase A Task A5).

A5 moves construction/auth glue only: services/http_client.py remains the
single shared runtime transport mechanism (pool, token buckets, backoff,
telemetry — all its own tests in test_http_client.py are the baseline
behavior A5 must not change), and transport.py re-exports call_with_backoff
so later services/kalshi/ modules import it from the boundary rather than
reaching into services.http_client directly.

Key-handling tests use a throwaway RSA key generated on the fly (same
convention as test_kalshi_account_client.py) — never a real credential,
never the network.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services import http_client
from services.kalshi import transport


def _generate_test_key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_build_public_client_configures_host_without_credentials():
    client = transport.build_public_client("https://example.test/trade-api/v2/")

    assert client.configuration.host == "https://example.test/trade-api/v2"
    assert not getattr(client.configuration, "api_key_id", None)


def test_build_account_client_configures_host_key_id_and_pem():
    pem = _generate_test_key_pem()

    client = transport.build_account_client(
        "https://example.test/trade-api/v2/", key_id="test-key-id", private_key_pem=pem
    )

    assert client.configuration.host == "https://example.test/trade-api/v2"
    assert client.configuration.api_key_id == "test-key-id"
    assert client.configuration.private_key_pem == pem


def test_build_account_client_rejects_malformed_pem_before_touching_the_sdk():
    with pytest.raises(ValueError):
        transport.build_account_client(
            "https://example.test/trade-api/v2", key_id="test-key-id",
            private_key_pem=b"this is not a PEM key",
        )


def test_call_with_backoff_is_the_shared_http_client_implementation():
    """The boundary re-exports, never re-implements: one retry/limiter/
    telemetry stack (design-spec constraint 'do not duplicate connection
    pools or rate limiters')."""
    assert transport.call_with_backoff is http_client.call_with_backoff


def test_transport_declares_contract_docs_for_its_operations():
    docs = transport.CONTRACT_DOCS

    assert "build_public_client" in docs
    assert "build_account_client" in docs
    assert any("api_environments" in d for d in docs["build_public_client"])
    assert any("api_keys" in d for d in docs["build_account_client"])
