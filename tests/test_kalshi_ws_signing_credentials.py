"""KalshiStreamGateway.signing_credentials() (issue #260) - exposes this
connection's already-loaded Kalshi API credentials so services/index_feed/
backfill.py can sign the CF Benchmarks REST passthrough call without
re-reading (or re-validating) the private key file a second time.

Throwaway RSA key generated on the fly (cryptography's own key generator),
never a real Kalshi key - same isolation as tests/test_kalshi_account_client.py's
credential-loading tests."""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.kalshi.websocket import KalshiStreamGateway


def _generate_test_key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_signing_credentials_is_none_without_credentials(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")

    assert gw.enabled is False
    assert gw.signing_credentials() is None


def test_signing_credentials_returns_the_loaded_key_id_and_private_key(tmp_path, monkeypatch):
    key_path = tmp_path / "test_key.pem"
    key_path.write_bytes(_generate_test_key_pem())
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))

    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")

    assert gw.enabled is True
    credentials = gw.signing_credentials()
    assert credentials is not None
    key_id, private_key = credentials
    assert key_id == "test-key-id"
    assert private_key is gw._private_key


def test_signing_credentials_is_none_when_the_key_file_fails_to_load(tmp_path, monkeypatch):
    key_path = tmp_path / "bad_key.pem"
    key_path.write_text("this is not a PEM key")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))

    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")

    assert gw.enabled is False
    assert gw.signing_credentials() is None
