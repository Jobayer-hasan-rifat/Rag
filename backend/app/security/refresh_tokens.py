import hashlib
import secrets

REFRESH_TOKEN_BYTES = 48


def generate_refresh_token() -> str:
    """Opaque, high-entropy secret. Only its SHA-256 digest is ever stored."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    # SHA-256 (not bcrypt) is appropriate: the input is 384 bits of randomness, not a human secret.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
