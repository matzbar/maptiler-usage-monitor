# MapTiler Usage Monitor

API que acompanha o consumo de uma conta [MapTiler Cloud](https://www.maptiler.com/cloud/) pela
[Analytics API](https://docs.maptiler.com/cloud/admin-api/analytics/): requests e sessions do período de
cobrança, porcentagem da cota, faixa de consumo, projeção até o fim do período e histórico dia a dia.

Outros projetos consultam o consumo por esta API, cada um com a própria chave, sem nunca receber a credencial de
serviço do MapTiler, que dá acesso total à conta e fica só aqui.

> **Situação:** em desenvolvimento. Pronto: consulta ao MapTiler, cálculo, histórico e leitura pela API.
> Próximas etapas: alertas por e-mail (Resend) e WhatsApp (CallMeBot), agendamento a cada 6 horas pelo
> GitHub Actions e painel do administrador.

## Como funciona

1. Uma verificação (`POST /v1/checks`) faz 4 consultas à Analytics API (requests e sessions, por serviço e por
   chave), calcula o consumo e grava o resultado no MongoDB.
2. Os projetos leem o consumo guardado (`GET /v1/usage`). Se a última verificação tiver mais de 6 horas, a leitura
   responde na hora e dispara uma verificação nova em segundo plano.
3. Uma falha na consulta fica gravada como falha (`status: failed`) e **nunca vira consumo zero**: a leitura
   continua mostrando o último valor válido, com a data, e informa o erro em `last_error`.

Faixas (`level`), pela maior porcentagem entre requests e sessions: `normal` (abaixo de 50%), `notice` (50%),
`warning` (70%), `high` (80%), `very_high` (90%) e `critical` (100%). No plano Free, ao atingir 100% o MapTiler
pausa o serviço até o próximo período.

## Rodar localmente

Requer Docker. A credencial de serviço vem de cloud.maptiler.com/account/credentials.

```bash
cp .env.example .env    # preencha MAPTILER_SERVICE_TOKEN, CHECK_TOKEN e ADMIN_TOKEN (o .env fica fora do Git)
docker compose up -d --build              # API em http://127.0.0.1:8000 (MONITOR_PORT=8010 para outra porta)
curl -X POST -H "Authorization: Bearer $CHECK_TOKEN" http://127.0.0.1:8000/v1/checks
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:8000/v1/usage
```

A documentação interativa fica em `/docs`, e a especificação OpenAPI em `/openapi.json`. Com ela, dá para gerar
um cliente em qualquer linguagem.

Para gerar um token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Rotas

| Rota | Quem usa | O que faz |
| --- | --- | --- |
| `GET /health` | Hospedagem (health check) | Diz se a API está no ar; sem dados de consumo |
| `POST /v1/checks` | Agendador (`CHECK_TOKEN`) ou administrador | Consulta o MapTiler e grava; 502 se a consulta falhar, 429 se a última foi há menos de 10 minutos |
| `GET /v1/usage` | Projetos clientes ou administrador | Consumo do período, percentuais, faixa, projeção, detalhe por serviço e por chave |
| `GET /v1/usage/history?days=30` | Projetos clientes ou administrador | Consumo dia a dia (1 a 400 dias) |
| `GET /docs`, `GET /openapi.json` | Quem for integrar | Documentação gerada automaticamente |

Exemplo de `GET /v1/usage` (consumo real de 14/11/2026, reduzido):

```json
{
  "status": "ok",
  "checked_at": "2026-11-14T20:12:54.107000Z",
  "period": {"since": "2026-11-14", "until": "2026-12-13"},
  "requests": {"used": 2186, "quota": 100000, "percent": 2.1},
  "sessions": {"used": 0, "quota": 5000, "percent": 0.0},
  "level": "normal",
  "projection": {"requests": 65580, "requests_percent": 65.5, "sessions": 0, "sessions_percent": 0.0,
                 "based_on_days": 1, "days_in_period": 30},
  "keys": [{"key": "7lbZ…aC", "name": "API Key do maptiler para o HydrantRouteSP", "requests": 2186, "sessions": 0}],
  "last_error": null
}
```

`status`: `ok` (dado recente), `stale` (dado com mais de 12 horas ou de um período encerrado) ou `unavailable`
(nenhuma verificação deu certo ainda; os números vêm vazios). As porcentagens são truncadas, nunca arredondadas
para cima. Com poucos dias de período, a projeção oscila bastante.

## Projetos clientes

Todas as rotas de dados usam `Authorization: Bearer <token>`. Cada projeto tem a sua chave, criada por um script
que a mostra **uma única vez**. O banco guarda só o hash dela.

```bash
docker compose exec api python -m scripts.projetos criar HydrantRouteSP --chave-maptiler <chave pública do mapa>
docker compose exec api python -m scripts.projetos listar
docker compose exec api python -m scripts.projetos desativar HydrantRouteSP
```

`--chave-maptiler` (pode repetir) define quais chaves do MapTiler o projeto vê no detalhe por chave. Os totais da
conta aparecem para todos, porque a cota é da conta inteira.

Exemplo em Python:

```python
import httpx

r = httpx.get("https://SEU-MONITOR/v1/usage", headers={"Authorization": f"Bearer {CHAVE_DO_PROJETO}"}, timeout=3)
uso = r.json()
if uso["status"] != "unavailable" and uso["requests"]["percent"] >= 95:
    ...  # por exemplo, trocar para um mapa alternativo
```

## Configuração

Todas as variáveis, com explicação e valor padrão, estão em [`.env.example`](.env.example). As obrigatórias são:
`MAPTILER_SERVICE_TOKEN`, `MONGO_URL`, `CHECK_TOKEN` e `ADMIN_TOKEN`. As cotas (`QUOTA_REQUESTS` e
`QUOTA_SESSIONS`) mudam junto com o plano do MapTiler.

## Dados guardados

Database `DB_NAME` (padrão `maptiler_monitor`):

- `maptiler_usage_monitor`: uma verificação por documento, apagada automaticamente depois de `RETENTION_DAYS`
  (padrão 400 dias);
- `clients`: projetos clientes (nome, hash da chave, chaves do MapTiler, ativo ou não).

No MongoDB Atlas, use um usuário com acesso só a esse database.

## Testes

```bash
pip install -r requirements-dev.txt
pytest                                            # sem internet: o MapTiler é simulado
MONGO_TEST_URL=mongodb://localhost:27017 pytest   # inclui os testes com um MongoDB de verdade
```

O CI roda os testes (com MongoDB), o `pip-audit` e o build e teste da imagem Docker.

## Segurança

- A credencial de serviço fica só na variável `MAPTILER_SERVICE_TOKEN`. Ela nunca aparece em respostas nem em
  logs, e o código só faz GET na rota de consumo do MapTiler, sem seguir redirecionamentos.
- Cada papel tem o seu token: projeto (só lê o consumo), agendador (só dispara verificações) e administrador.
  As comparações são feitas em tempo constante.
- Há limite de requisições por IP. As respostas levam cabeçalhos de segurança (CSP, HSTS em HTTPS,
  `X-Frame-Options`) e `Cache-Control: no-store` nos dados.
- Os logs são estruturados (uma linha JSON por evento) e não contêm segredos.
- A imagem Docker roda sem root.

## Licença

[MIT](LICENSE)
