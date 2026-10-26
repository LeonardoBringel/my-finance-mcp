"""my-finance-mcp-token: issues personal tokens from the command line."""

import argparse
import logging
import re
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone

from my_finance_mcp.auth.tokens import SECONDS_PER_DAY, issue_token
from my_finance_mcp.config import load_settings_or_exit
from my_finance_mcp.data.crypto import hash_for_lookup
from my_finance_mcp.data.db import DatabaseUnavailableError, create_db_engine
from my_finance_mcp.data.repositories.users_repository import (
    find_user_by_username_hash,
)

DAYS_PATTERN = r"[0-9]+"
EXPIRY_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-finance-mcp-token",
        description="Emite tokens pessoais do my-finance-mcp.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser(
        "issue",
        help="emite um token para um usuário do my-finance",
        description="Escreve o token no stdout e o resumo no stderr.",
    )
    issue.add_argument(
        "--username", required=True, help="username no my-finance"
    )
    # Read as a string: the range check below owns the message and exit code,
    # which type=int would replace with argparse's own.
    issue.add_argument(
        "--days", help="validade em dias (padrão e teto: MCP_TOKEN_MAX_DAYS)"
    )
    return parser


def _report(message: str, code: int) -> int:
    # Messages go to stderr only, so stdout can be redirected with the token.
    print(message, file=sys.stderr)
    return code


def _issue(username: str, raw_days: str | None) -> int:
    # Exits with code 1 on invalid configuration, before the database.
    settings = load_settings_or_exit()
    max_days = settings.MCP_TOKEN_MAX_DAYS

    if not username.strip():
        return _report("--username é obrigatório.", 2)

    if raw_days is None:
        days = max_days
    elif (
        re.fullmatch(DAYS_PATTERN, raw_days) and 1 <= int(raw_days) <= max_days
    ):
        days = int(raw_days)
    else:
        return _report(f"--days deve estar entre 1 e {max_days}.", 2)

    # The username is hashed as typed, without strip: it must match what
    # my-finance stored byte for byte. It never appears in any message.
    username_hash = hash_for_lookup(
        username, settings.FERNET_KEY.get_secret_value()
    )
    engine = create_db_engine(settings)
    try:
        user = find_user_by_username_hash(engine, username_hash)
    except DatabaseUnavailableError:
        return _report("Banco de dados indisponível.", 1)
    finally:
        engine.dispose()
    if user is None:
        return _report("Usuário não encontrado.", 1)

    # Whole seconds, so the expiry printed below is the token's exp claim.
    now = int(time.time())
    token = issue_token(settings, user.user_id, user.token_version, days, now)
    expires_at = datetime.fromtimestamp(
        now + days * SECONDS_PER_DAY, timezone.utc
    )
    print(token)
    return _report(
        f"Token emitido para o usuário {user.user_id}, "
        f"válido até {expires_at.strftime(EXPIRY_FORMAT)}.",
        0,
    )


def main(argv: Sequence[str] | None = None) -> int:
    # Installed before loading settings so configuration errors are printed
    # with their level, on stderr.
    logging.basicConfig()
    args = _build_parser().parse_args(argv)
    return _issue(args.username, args.days)
