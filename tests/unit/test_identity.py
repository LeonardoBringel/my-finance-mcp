"""Fail-closed identity resolved from the SDK access token."""

import ast
from pathlib import Path

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser as SdkAuthenticatedUser,
)
from mcp.server.auth.provider import AccessToken

from my_finance_mcp.auth.identity import (
    AuthenticatedUser,
    MissingIdentityError,
    current_user,
)

SRC = Path(__file__).resolve().parents[2] / "src"

INVALID_SUBJECTS = [None, "0", "042", "-1", "2147483648", "abc", "42\n"]

INVALID_CLAIMS = [None, {}, {"jti": ""}, {"jti": 123}]


def resolve(subject, claims):
    token = AccessToken(
        token="opaque",
        client_id="client",
        scopes=[],
        subject=subject,
        claims=claims,
    )
    reset_token = auth_context_var.set(SdkAuthenticatedUser(token))
    try:
        return current_user()
    finally:
        auth_context_var.reset(reset_token)


def test_valid_token_returns_user():
    user = resolve("42", {"jti": "j-1"})

    assert user == AuthenticatedUser(user_id=42, jti="j-1")
    assert type(user.user_id) is int


def test_max_int32_subject_is_accepted():
    user = resolve("2147483647", {"jti": "j-1"})

    assert user == AuthenticatedUser(user_id=2147483647, jti="j-1")


def test_missing_token_raises():
    assert auth_context_var.get() is None

    with pytest.raises(MissingIdentityError):
        current_user()


@pytest.mark.parametrize("subject", INVALID_SUBJECTS)
def test_invalid_subject_raises(subject):
    with pytest.raises(MissingIdentityError):
        resolve(subject, {"jti": "j-1"})


@pytest.mark.parametrize("claims", INVALID_CLAIMS)
def test_invalid_jti_raises(claims):
    with pytest.raises(MissingIdentityError):
        resolve("42", claims)


@pytest.mark.parametrize(
    ("subject", "claims", "received"),
    [
        ("2147483648", {"jti": "ZZJTI-subject-case"}, ["2147483648", "ZZJTI"]),
        ("987654", {"jti": 123456789}, ["987654", "123456789"]),
    ],
)
def test_error_message_omits_received_values(subject, claims, received):
    with pytest.raises(MissingIdentityError) as exc_info:
        resolve(subject, claims)

    message = str(exc_info.value)
    assert message
    for value in received:
        assert value not in message


def test_only_identity_module_reads_access_token():
    readers = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "get_access_token" in path.read_text(encoding="utf-8")
    )

    assert readers == ["my_finance_mcp/auth/identity.py"]


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Ids of the constants that are module, class or function docstrings."""
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return docstrings


def _header_literals(root: Path) -> list[str]:
    """String constants under root, outside docstrings, naming the header."""
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or id(node) in docstrings:
                continue
            value = node.value
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            if isinstance(value, str) and "authorization" in value.lower():
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    return offenders


def test_src_has_no_authorization_literal_outside_docstrings():
    assert _header_literals(SRC) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('HEADER = "Authorization"\n', ["module.py:1"]),
        ("HEADER = b'AUTHORIZATION'\n", ["module.py:1"]),
        (
            'def read(headers):\n    return headers.get(f"authorization")\n',
            ["module.py:2"],
        ),
    ],
)
def test_authorization_literal_sweep_flags_code_constants(
    tmp_path, source, expected
):
    (tmp_path / "module.py").write_text(source, encoding="utf-8")

    assert _header_literals(tmp_path) == expected


def test_authorization_literal_sweep_ignores_docstrings_and_comments(tmp_path):
    source = (
        '"""Routes without Authorization."""\n'
        "# The Authorization header is read by the SDK.\n"
        "class Route:\n"
        '    """Reads no Authorization header."""\n'
        "    def handle(self):\n"
        '        """Authorization is checked upstream."""\n'
        "        return 1\n"
    )
    (tmp_path / "module.py").write_text(source, encoding="utf-8")

    assert _header_literals(tmp_path) == []
