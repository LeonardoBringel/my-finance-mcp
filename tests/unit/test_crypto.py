"""Lookup hash for encrypted columns."""

import pytest

from my_finance_mcp.data.crypto import hash_for_lookup

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
