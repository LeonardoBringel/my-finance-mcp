# my-finance-mcp

Servidor MCP somente leitura sobre o banco do my-finance. Expõe os dados
financeiros a clientes MCP sem conseguir alterar nada: a engine abre toda
conexão em modo read-only e o role `my_finance_mcp` só tem `SELECT` nas
tabelas necessárias.

Rotas HTTP:

- `GET /health`: liveness, responde `{"status": "ok"}` sem tocar no banco.
- `GET /ready`: readiness, confere se o banco responde com uma revisão de
  schema suportada.
- `POST /mcp`: endpoint MCP (Streamable HTTP, sem sessão, respostas em
  JSON). Só aceita os hosts de `MCP_ALLOWED_HOSTS` e as origens de
  `MCP_ALLOWED_ORIGINS`.

## Pré-requisitos

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker e Docker Compose, para rodar no homelab
- Acesso de superusuário ao PostgreSQL do my-finance, para criar o role

## Desenvolvimento

Copie `.env.sample` para `.env` e preencha os valores `change-me`. Sem uma
configuração válida o serviço não sobe: ele encerra com código 1 e loga uma
linha por variável inválida.

| Alvo           | O que faz                                        |
| -------------- | ------------------------------------------------ |
| `make install` | Instala as dependências com `uv sync`            |
| `make run`     | Sobe o servidor com `uvicorn --factory`          |
| `make test`    | Roda a suíte com `pytest`                        |
| `make lint`    | Confere a formatação com `black` e `isort`       |

Para rodar os mesmos gates a cada commit, instale o hook do pre-commit:

```bash
uv run pre-commit install
```

### Testar o endpoint MCP localmente

Para chamar `/mcp` pelo `localhost`, libere o host no `.env`:

```bash
MCP_ALLOWED_HOSTS=localhost:*
```

Com o servidor rodando (`make run`), abra o MCP Inspector e conecte em
`http://localhost:8000/mcp` com o transporte Streamable HTTP:

```bash
npx @modelcontextprotocol/inspector
```

## Role do banco

O script `deploy/db/create_role.sql` cria o role `my_finance_mcp` e converge
os grants. Pode ser rodado de novo sem efeito colateral. Sem a variável
`mcp_password`, ele só imprime o uso e sai sem alterar nada.

```bash
psql -v ON_ERROR_STOP=1 -v mcp_password="$MCP_DB_PASSWORD" -d finance -f deploy/db/create_role.sql
```

## Deploy

O serviço entra na rede `my-finance_finance_net` do my-finance e publica a
porta só em `127.0.0.1:8010`, para ser alcançado pelo proxy local.

```bash
docker compose up -d --build
```

```bash
curl http://127.0.0.1:8010/ready
```
