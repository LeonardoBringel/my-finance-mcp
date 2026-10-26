"""Lookup hash for encrypted columns."""

import hashlib
import hmac


def hash_for_lookup(value: str, fernet_key: str) -> str:
    """HMAC-SHA256 of value, keyed by the FERNET_KEY text, as lowercase hex.

    The key is used as typed (UTF-8 of the base64 text, never decoded) and the
    value is not normalized, so it must match what was stored byte for byte.
    """
    if not value:
        return ""
    return hmac.new(
        fernet_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()
