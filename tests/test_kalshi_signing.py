"""services/kalshi/signing.py - the RSA-PSS request-signing helper the CF
Benchmarks REST passthrough backfill (issue #260) needs because that
endpoint has no typed SDK method to sign it automatically. Verified against
docs/kalshi/api_keys.md / docs/kalshi/quick_start_authenticated_requests.md's
own signing algorithm and sample code (`timestamp + METHOD + path`,
RSA-PSS/SHA256, base64 - path signed WITHOUT its query string).

PSS signatures are randomized (a fresh salt every call), so two calls for
the same inputs never produce byte-identical signatures - correctness is
checked by having the matching public key verify the signature against the
expected message, not by comparing signature bytes."""
import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.kalshi import signing


def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _assert_verifies(public_key, headers: dict, message: bytes) -> None:
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    public_key.verify(
        signature, message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_sign_request_produces_a_signature_the_public_key_verifies():
    private_key, public_key = _keypair()

    headers = signing.sign_request(
        "key-1", private_key, "GET", "/trade-api/v2/cfbenchmarks/history/values", now_ms=1703123456789,
    )

    assert headers["KALSHI-ACCESS-KEY"] == "key-1"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1703123456789"
    message = b"1703123456789GET/trade-api/v2/cfbenchmarks/history/values"
    _assert_verifies(public_key, headers, message)


def test_sign_request_strips_the_query_string_before_signing():
    """Both mirrored doc pages warn that signing a path WITH its query
    string produces a signature mismatch - their own sample code strips it
    with `path.split('?')[0]` - so this defends a caller that forgets to
    (services/index_feed/backfill.py builds a query string for id/timespan/
    timestamp on the same path)."""
    private_key, public_key = _keypair()

    headers = signing.sign_request(
        "key-1", private_key, "GET",
        "/trade-api/v2/cfbenchmarks/history/values?id=BRTI&timespan=HOUR",
        now_ms=1703123456789,
    )

    message = b"1703123456789GET/trade-api/v2/cfbenchmarks/history/values"
    _assert_verifies(public_key, headers, message)


def test_sign_request_defaults_timestamp_to_now_in_milliseconds(monkeypatch):
    private_key, _ = _keypair()
    monkeypatch.setattr(signing.time, "time", lambda: 1703123456.789)

    headers = signing.sign_request("key-1", private_key, "GET", "/trade-api/v2/x")

    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1703123456789"


def test_sign_request_a_wrong_message_fails_verification():
    """Negative control: prove _assert_verifies would actually catch a
    mismatch rather than passing vacuously."""
    private_key, public_key = _keypair()
    headers = signing.sign_request("key-1", private_key, "GET", "/trade-api/v2/x", now_ms=1)

    with pytest.raises(InvalidSignature):
        _assert_verifies(public_key, headers, b"1GET/trade-api/v2/WRONG")
