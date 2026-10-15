"""Static checks on the docker-compose file."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def service() -> dict:
    return compose()["services"]["my-finance-mcp"]


def test_service_is_built_from_the_repository_root():
    assert service()["build"] == "."


def test_service_has_the_expected_container_name():
    assert service()["container_name"] == "my-finance-mcp"


def test_service_restarts_unless_stopped():
    assert service()["restart"] == "unless-stopped"


def test_service_loads_the_env_file():
    env_file = service()["env_file"]

    assert ".env" in ([env_file] if isinstance(env_file, str) else env_file)


def test_service_points_at_the_database_host_and_sao_paulo_timezone():
    environment = service()["environment"]

    assert environment["DB_HOST"] == "db"
    assert environment["TZ"] == "America/Sao_Paulo"


def test_service_port_is_published_only_on_loopback():
    assert service()["ports"] == ["127.0.0.1:8010:8000"]


def test_service_joins_the_external_my_finance_network():
    config = compose()
    used = service()["networks"]

    assert used
    assert any(
        config["networks"][name]
        == {"external": True, "name": "my-finance_finance_net"}
        for name in used
    )
