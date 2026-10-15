-- Provisions the read-only database role used by my-finance-mcp.
--
-- Run as a superuser, against the my-finance database. Safe to re-run: it
-- converges to the same privileges every time.
--
--   psql -v ON_ERROR_STOP=1 -v mcp_password="$MCP_DB_PASSWORD" \
--        -d finance -f deploy/db/create_role.sql

-- Refuse to run without the password variable, before any change.
\if :{?mcp_password}
\else
  \echo 'Usage: psql -v ON_ERROR_STOP=1 -v mcp_password=... -d finance -f deploy/db/create_role.sql'
  \quit
\endif

-- CREATE ROLE has no IF NOT EXISTS: generate it only when the role is missing.
SELECT 'CREATE ROLE my_finance_mcp'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'my_finance_mcp')
\gexec

BEGIN;

ALTER ROLE my_finance_mcp WITH LOGIN PASSWORD :'mcp_password';
ALTER ROLE my_finance_mcp SET default_transaction_read_only = on;

-- Revoking table privileges also revokes column privileges, so re-runs
-- converge the grant on users.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM my_finance_mcp;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM my_finance_mcp;

GRANT USAGE ON SCHEMA public TO my_finance_mcp;
GRANT SELECT ON transactions, categories, alembic_version TO my_finance_mcp;
GRANT SELECT (id, username_hash, token_version) ON users TO my_finance_mcp;

COMMIT;
