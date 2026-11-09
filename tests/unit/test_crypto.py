"""Lookup hash and decryption of encrypted columns."""

from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from my_finance_mcp.data.crypto import decrypt, decrypt_decimal, hash_for_lookup

FERNET_KEY = "WlpTRUNSRVQtZmVybmV0LWtleS0zMi1ieXRlcy1vayE="
OTHER_FERNET_KEY = "b3V0cmEtY2hhdmUtZmVybmV0LTMyLWJ5dGVzLW9rISE="

ALICE_HASH = "58bdcfa5c47eb8f88d243d53939c2f5e7600bcfa9c8ad9d509e7c601917f4d95"
JOAO_HASH = "2ff2b7e43b737e4979a5bcaf17ae306979b0ad3a880ec9e947569f511414b29d"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("alice", ALICE_HASH), ("joão", JOAO_HASH)],
)
def test_matches_fixed_vectors(value, expected):
    assert hash_for_lookup(value, FERNET_KEY) == expected


def test_leading_space_is_not_stripped():
    assert hash_for_lookup(" alice", FERNET_KEY) != hash_for_lookup(
        "alice", FERNET_KEY
    )


def test_trailing_space_is_not_stripped():
    assert hash_for_lookup("alice ", FERNET_KEY) != ALICE_HASH


def test_is_case_sensitive():
    assert hash_for_lookup("Alice", FERNET_KEY) != hash_for_lookup(
        "alice", FERNET_KEY
    )


def test_empty_value_returns_empty_string():
    assert hash_for_lookup("", FERNET_KEY) == ""


def test_different_key_gives_different_hash():
    assert hash_for_lookup("alice", OTHER_FERNET_KEY) != ALICE_HASH


@pytest.fixture
def fernet():
    return Fernet(Fernet.generate_key())


@pytest.mark.parametrize("text", ["Mercado", "Saúde", "33.33", "2026-01-05"])
def test_decrypt_token_of_the_same_key_returns_original_text(fernet, text):
    token = fernet.encrypt(text.encode("utf-8")).decode("ascii")

    assert decrypt(token, fernet) == text


def test_decrypt_token_of_another_key_returns_token_as_is(fernet):
    token = Fernet(Fernet.generate_key()).encrypt(b"Mercado").decode("ascii")

    assert decrypt(token, fernet) == token


@pytest.mark.parametrize("text", ["Mercado", "Saúde", "33.33", "gAAAAé"])
def test_decrypt_plain_text_returns_text_as_is(fernet, text):
    assert decrypt(text, fernet) == text


@pytest.mark.parametrize("token", [None, ""])
def test_decrypt_none_or_empty_returns_none(fernet, token):
    assert decrypt(token, fernet) is None


def test_decrypt_token_with_non_utf8_payload_returns_token_as_is(fernet):
    token = fernet.encrypt(b"\xff\xfe\xfa").decode("ascii")

    assert decrypt(token, fernet) == token


def test_decrypt_decimal_of_encrypted_number(fernet):
    token = fernet.encrypt(b"33.33").decode("ascii")

    value = decrypt_decimal(token, fernet)

    assert type(value) is Decimal
    assert value == Decimal("33.33")
    assert str(value) == "33.33"


def test_decrypt_decimal_of_plain_text_number(fernet):
    value = decrypt_decimal("33.33", fernet)

    assert type(value) is Decimal
    assert str(value) == "33.33"


def test_decrypt_decimal_of_encrypted_non_number_is_zero(fernet):
    token = fernet.encrypt(b"nan").decode("ascii")

    assert decrypt_decimal(token, fernet) == Decimal("0")


@pytest.mark.parametrize(
    "token", [None, "", "abc", "1,50", "nan", "inf", "-Infinity"]
)
def test_decrypt_decimal_of_unusable_value_is_zero(fernet, token):
    value = decrypt_decimal(token, fernet)

    assert type(value) is Decimal
    assert value.is_finite()
    assert value == Decimal("0")
