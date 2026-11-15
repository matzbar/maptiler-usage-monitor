# MapTiler Usage Monitor

API que acompanha o consumo de uma conta [MapTiler Cloud](https://www.maptiler.com/cloud/) pela
[Analytics API](https://docs.maptiler.com/cloud/admin-api/analytics/): requests e sessions do período de
cobrança, porcentagem da cota, faixa de consumo, projeção até o fim do período e histórico dia a dia.

Outros projetos consultam o consumo por esta API, cada um com a própria chave, sem nunca receber a credencial de
serviço do MapTiler, que dá acesso total à conta e fica só aqui.

> **Situação:** em desenvolvimento. Pronto: consulta ao MapTiler, cálculo, histórico, leitura pela API, alertas
> por e-mail (Resend) e WhatsApp (CallMeBot), relatório diário por e-mail, painel do administrador e agendamento
> pelo GitHub Actions (desligado até o monitor ser publicado). Próxima etapa: publicação no Render, com o passo
> a passo em [`DEPLOY.md`](DEPLOY.md).

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

## Alertas

Depois de cada verificação, requests e sessions são avaliados **separadamente**:

| Consumo da cota | O que acontece |
| --- | --- |
| 50% e 70% | Fica registrado (para o painel) |
| 80% | E-mail |
| 90% | E-mail e WhatsApp |
| 100% | E-mail e WhatsApp (cota esgotada) |

- **Uma vez por faixa, métrica, canal e período.** Cada aviso tem um identificador único no banco
  (`período:métrica:faixa:canal`), então nem duas verificações simultâneas repetem o envio. No período seguinte,
  as faixas voltam a valer.
- **Salto de faixa:** de 60% direto para 92%, sai só o aviso de 90%; o de 80% fica registrado como superado.
- **Falha no envio:** é tentado de novo nas verificações seguintes (até `ALERT_MAX_ATTEMPTS` vezes, com pelo menos
  10 minutos entre tentativas). O e-mail usa a chave de idempotência do Resend, para não chegar duplicado.
- **Monitor sem dados:** depois de `ALERT_FAILURE_STREAK` verificações seguidas com falha, um e-mail, uma vez por
  sequência de falhas.
- As faixas e os canais mudam em `ALERT_RULES`. Canal não configurado não falha: o aviso fica como `skipped`.
- No WhatsApp, `sent` quer dizer que o CallMeBot aceitou a mensagem na fila dele. No plano gratuito, ela pode
  levar de 15 a 20 minutos para chegar (observado em nov/2026). O e-mail chega em segundos, e por isso vai junto
  em todas as faixas que usam WhatsApp.

Para conferir os canais depois de configurar: `POST /v1/alerts/test` (administrador) manda uma mensagem de teste
em cada um.

**Relatório diário:** `POST /v1/reports/daily` manda por e-mail o consumo do período, a projeção, as verificações
e os avisos das últimas 24 horas. Sai no máximo um por dia (no fuso `ALERT_TIMEZONE`); chamadas repetidas
respondem `already_sent`.

## Painel

Em `/panel`, para o administrador: situação e faixa, medidores de requests e sessions com as faixas de aviso e a
projeção, gráfico do consumo acumulado contra a cota, consumo por chave e por serviço, avisos com o resultado de
cada envio, verificações recentes e consumo dia a dia. Os botões verificam agora e mandam um aviso de teste.

- A página é estática e não traz dados: tudo vem das rotas `/v1` com o `ADMIN_TOKEN`, que a pessoa cola na
  tela de entrada. O token fica só naquela aba (`sessionStorage`) e some quando ela é fechada.
- A política de segurança da página só permite script, estilo e conexões do próprio monitor, sem nada inline
  nem de outros sites. Os textos que vêm da API entram como texto, nunca como HTML.
- Cada atualização do painel faz 4 requisições, que contam no limite por IP (`RATE_LIMIT_PER_MIN`). Os arquivos
  estáticos do painel (`/panel/static/`) não contam.
- Visual inspirado no console do MapTiler Cloud (fontes Ubuntu e Open Sans, servidas pelo próprio monitor), com
  tema claro e escuro.

Código em `app/panel/`, em módulos JavaScript sem build nem dependências:

| Arquivo | Responsabilidade |
| --- | --- |
| `js/main.js` | Entrada, carregamento, botões e estado da tela |
| `js/messages.js` | Mensagens mostradas depois de "Verificar agora" e "Testar avisos" |
| `js/api.js` | Cliente das rotas `/v1` (token, tempo limite, erros) |
| `js/sections.js` | Uma função de desenho por seção |
| `js/chart-model.js`, `js/chart.js` | Cálculo e desenho do gráfico |
| `js/format.js`, `js/labels.js` | Números e datas no formato brasileiro; textos de cada código da API |
| `js/dom.js`, `js/token-store.js` | Criação de elementos (sempre como texto); onde o token fica guardado |

Navegadores suportados: Chrome/Edge 103+, Firefox 100+ e Safari 16+. Contraste de cores no nível AA da WCAG, nos
temas claro e escuro. Mudanças no painel: `npm install` uma vez, depois `npm run format` e `npm run lint`
(ESLint, Stylelint e Prettier; o `package.json` só tem ferramentas de desenvolvimento).

## Agendamento

O workflow [`scheduler.yml`](.github/workflows/scheduler.yml) chama o monitor pelo GitHub Actions:

| Horário (Brasília) | Rotina |
| --- | --- |
| 3h17, 15h17 e 21h17 | Verificação do consumo (e, com ela, os alertas) |
| 9h17 | Verificação e relatório diário por e-mail |

Para ligar, crie dois segredos em *Settings > Secrets and variables > Actions*: `MONITOR_URL` (endereço público do
monitor, com `https://`) e `MONITOR_CHECK_TOKEN` (o mesmo valor de `CHECK_TOKEN` no monitor). Sem eles, cada
execução só avisa "agendamento desligado" e termina sem erro. Para testar na hora: aba *Actions > Scheduler >
Run workflow*.

- Antes das rotinas, o script ([`scripts/scheduler.py`](scripts/scheduler.py)) chama `GET /health` até o monitor
  responder (até cerca de 1,5 minuto): hospedagens gratuitas desligam o serviço parado. As rotinas são chamadas uma
  vez só: uma verificação que falhou também conta para o intervalo mínimo, e repeti-la só daria "muito cedo".
- "Verificação recente", "verificação em andamento" e "relatório já enviado" não são falhas. Falha do MapTiler,
  token recusado ou monitor fora do ar deixam o job vermelho, e o GitHub manda e-mail para quem alterou o workflow
  por último: é o aviso de que o monitor parou. O monitor também avisa por e-mail depois de 3 verificações seguidas
  com falha.
- O token só vai por `https://` (ou para `localhost`) e nunca aparece no log.
- Em repositório público, o GitHub pausa os agendamentos depois de 60 dias sem atividade no repositório e avisa
  por e-mail; para religar, basta um clique em *Enable workflow* na aba Actions. O horário também não é exato:
  em momentos de fila, uma execução pode atrasar alguns minutos.

## Publicação

No Render (plano gratuito) com o banco no MongoDB Atlas: [`DEPLOY.md`](DEPLOY.md) tem o passo a passo, e o
[`render.yaml`](render.yaml) traz a configuração do serviço (Docker, `/health`, deploy só depois do CI). Os
segredos são preenchidos no Render, nunca no repositório.

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
| `GET /v1/checks?limit=20` | Administrador | Verificações recentes, com sucesso ou falha (1 a 200) |
| `GET /v1/alerts?limit=50` | Administrador | Histórico de avisos, com o resultado de cada envio |
| `POST /v1/alerts/test` | Administrador | Manda um aviso de teste em cada canal configurado (1 por minuto) |
| `POST /v1/reports/daily` | Agendador ou administrador | Manda o relatório diário por e-mail (1 por dia) |
| `GET /panel` | Administrador (navegador) | Painel; pede o `ADMIN_TOKEN` na própria página |
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
docker compose exec api python -m scripts.clients create HydrantRouteSP --maptiler-key <chave pública do mapa>
docker compose exec api python -m scripts.clients list
docker compose exec api python -m scripts.clients deactivate HydrantRouteSP
```

`--maptiler-key` (pode repetir) define quais chaves do MapTiler o projeto vê no detalhe por chave. Os totais da
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

Para os alertas:

- **E-mail (Resend):** crie uma conta em resend.com e uma chave em *API Keys* com permissão *Sending access*.
  Preencha `RESEND_API_KEY` e `ALERT_EMAIL_TO`. Sem domínio próprio verificado, o Resend só entrega no e-mail da
  própria conta do Resend.
- **WhatsApp (CallMeBot, gratuito para uso pessoal):** salve o número do CallMeBot nos contatos e mande para ele
  a mensagem de ativação, como explica a
  [página do CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/). Preencha
  `ALERT_WHATSAPP_PHONE` (com +55) e `CALLMEBOT_API_KEY` (a chave que chega no WhatsApp).

## Dados guardados

Database `DB_NAME` (padrão `maptiler_monitor`):

- `maptiler_usage_monitor`: uma verificação por documento, apagada automaticamente depois de `RETENTION_DAYS`
  (padrão 400 dias);
- `maptiler_notification_history`: um aviso por documento (faixa, canal, status, tentativas, último erro),
  apagado depois do mesmo prazo;
- `clients`: projetos clientes (nome, hash da chave, chaves do MapTiler, ativo ou não).

No MongoDB Atlas, use um usuário com acesso só a esse database.

## Testes

```bash
pip install -r requirements-dev.txt
pytest                                            # sem internet: o MapTiler é simulado
MONGO_TEST_URL=mongodb://localhost:27017 pytest   # inclui os testes com um MongoDB de verdade
npm test                                          # painel: formatação, gráfico e cliente da API (Node 22+)

# painel num navegador de verdade (entrada, saída durante a carga, erros, ações, acessibilidade, celular)
pip install -r requirements-e2e.txt && python -m playwright install chromium
pytest tests/e2e                                  # com o Chrome instalado: E2E_BROWSER_CHANNEL=chrome pytest tests/e2e
```

O CI roda os testes (com MongoDB), o lint e os testes do painel, os testes no navegador, o `pip-audit` e o build e
teste da imagem Docker.

## Segurança

- A credencial de serviço fica só na variável `MAPTILER_SERVICE_TOKEN`. Ela nunca aparece em respostas nem em
  logs, e o código só faz GET na rota de consumo do MapTiler, sem seguir redirecionamentos.
- Cada papel tem o seu token: projeto (só lê o consumo), agendador (só dispara verificações e o relatório diário) e administrador.
  As comparações são feitas em tempo constante.
- Há limite de requisições por IP. As respostas levam cabeçalhos de segurança (CSP, HSTS em HTTPS,
  `X-Frame-Options`) e `Cache-Control: no-store` nos dados.
- Os logs são estruturados (uma linha JSON por evento) e não contêm segredos. A URL do CallMeBot (que leva
  telefone e chave) nunca é registrada, e os erros dos canais saem sem chave, telefone ou e-mail.
- A imagem Docker roda sem root.

## Licença

[MIT](LICENSE)
