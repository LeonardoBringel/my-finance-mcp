"""Lookup hash and decryption of encrypted columns."""

import hashlib
import hmac
from decimal import Decimal, InvalidOperation

from cryptography.fernet import Fernet, InvalidToken


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


def decrypt(token: str | None, fernet: Fernet) -> str | None:
    """Decrypted text, or the value as is when it is not a token of this key.

    Rows written before my-finance encrypted its columns hold plain text, so
    anything that does not decrypt is treated as that legacy plain text.
    """
    if not token:
        return None
    try:
        # Bytes, because Fernet raises a bare ValueError for a str with
        # non-ASCII characters, which legacy plain text often has.
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError):
        return token


def decrypt_decimal(token: str | None, fernet: Fernet) -> Decimal:
    """Decrypted number, or zero when it is missing, not a number or not finite.

    NaN and infinity become zero too: a total holding them does not serialize
    to JSON.
    """
    text = decrypt(token, fernet)
    if text is None:
        return Decimal("0")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return Decimal("0")
    if not value.is_finite():
        return Decimal("0")
    return value
