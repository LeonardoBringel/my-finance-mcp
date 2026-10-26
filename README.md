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

O endpoint exige um token pessoal (veja "Token pessoal"). No Inspector,
adicione em Authentication o header `Authorization` com o valor
`Bearer <token>`.

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

## Token pessoal

O `/mcp` só aceita requisições com um token pessoal no header
`Authorization: Bearer <token>`. O token é emitido por usuário do my-finance,
com o container em execução:

```bash
docker exec -it my-finance-mcp my-finance-mcp-token issue --username <username>
```

O token sai sozinho no stdout. O resumo, com o id do usuário e a data de
expiração, vai para o stderr. Copie o token na emissão: ele não fica
guardado em lugar nenhum e não é mostrado de novo.

`--days <n>` define a validade em dias, de 1 até `MCP_TOKEN_MAX_DAYS`. Sem
`--days`, o token vale `MCP_TOKEN_MAX_DAYS` dias, que é também o teto. Um
valor fora dessa faixa encerra com código 2 e não emite nada.

```bash
docker exec -it my-finance-mcp my-finance-mcp-token issue --username <username> --days 30
```

## Configurar o Claude Code

Guarde o token na variável `MY_FINANCE_MCP_TOKEN` e registre o servidor:

```bash
claude mcp add --transport http my-finance https://finance-mcp.<tailnet>.ts.net/mcp --header "Authorization: Bearer ${MY_FINANCE_MCP_TOKEN}"
```

## Revogar tokens

- **Um usuário:** o token carrega o `token_version` do usuário no momento da
  emissão. Incrementar o `token_version` invalida todos os tokens dele. O
  my-finance já faz isso na troca de senha.
- **Todos os usuários:** troque `MCP_TOKEN_SECRET` no `.env` e suba o serviço
  de novo. Os tokens assinados com o segredo antigo deixam de valer.

Depois de revogar, emita um novo token com `my-finance-mcp-token issue`.
