# Publicação do monitor

Passo a passo para colocar o monitor no ar no **Render** (plano gratuito), com o banco no **MongoDB Atlas**, e
ligar o agendamento no GitHub. Usa as mesmas contas do HydrantRouteSP. Tempo: uns 30 minutos, mais a espera do
primeiro deploy.

| O quê | Para quê | Custo |
| --- | --- | --- |
| Render | Deixa o monitor no ar em `https://...onrender.com` | Gratuito |
| MongoDB Atlas (o cluster do HydrantRouteSP) | Guarda o histórico de consumo e de avisos | Gratuito |
| MapTiler (credencial de serviço) | Lê o consumo da conta | Gratuito |
| Resend e CallMeBot | E-mail e WhatsApp dos avisos | Gratuito |
| GitHub Actions | Chama o monitor nos horários | Gratuito (repositório público) |

Guarde cada valor marcado com 🔑 num lugar seguro (um gerenciador de senhas). Nenhum deles vai para o repositório.

## Parte 1 — Banco no Atlas (5 min)

O monitor usa um database próprio, `maptiler_monitor`, no mesmo cluster do HydrantRouteSP, com um usuário que só
enxerga esse database.

1. Entre em **https://cloud.mongodb.com** e abra o projeto do HydrantRouteSP.
2. No menu da esquerda, **Database Access** → **Add New Database User**.
   - **Authentication Method:** Password.
   - **Username:** `maptiler-monitor`.
   - **Password:** clique em **Autogenerate Secure Password** (só letras e números) e 🔑 anote.
   - **Database User Privileges:** **Add Specific Privilege** → role **readWrite**, database
     **`maptiler_monitor`** (deixe *collection* em branco).
   - Clique em **Add User**.
3. **Network Access:** não precisa mexer. O acesso de qualquer lugar (`0.0.0.0/0`) já foi liberado para o
   HydrantRouteSP, e quem protege o banco é a senha.
4. Monte o 🔑 **`MONGO_URL`**: copie o `MONGO_URL` do HydrantRouteSP (Render → serviço do HydrantRouteSP →
   *Environment*) e troque só o usuário e a senha:
   ```
   mongodb+srv://maptiler-monitor:SENHA@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0
   ```
   Não precisa criar o database nem as coleções: o monitor cria sozinho quando sobe.
5. Confira com um comando, no Terminal, na pasta do projeto (ele pede o `MONGO_URL` sem mostrar na tela):
   ```
   .venv/bin/python -m scripts.check_mongo
   ```
   As três linhas devem terminar em "ok". O teste não deixa nada no banco, e o database `maptiler_monitor` só
   aparece no Atlas depois que o monitor subir.

## Parte 2 — Credencial de serviço do MapTiler (2 min)

1. Entre em **https://cloud.maptiler.com/account/credentials**.
2. Crie (ou reaproveite a dos testes) uma **credencial de serviço** e 🔑 anote: é o **`MAPTILER_SERVICE_TOKEN`**.

> Ela dá acesso total à conta do MapTiler. Fica só no Render: nunca no navegador, no HydrantRouteSP ou no Git.
> Não confunda com a chave pública do mapa (`MAPTILER_KEY` do HydrantRouteSP), que é outra.

## Parte 3 — E-mail e WhatsApp (5 min; pule o que já tiver)

- **E-mail (Resend):** em **https://resend.com/api-keys**, crie uma chave com permissão **Sending access** →
  🔑 **`RESEND_API_KEY`**. O **`ALERT_EMAIL_TO`** é o e-mail que recebe os avisos: sem domínio próprio no Resend,
  precisa ser o e-mail da sua conta do Resend.
- **WhatsApp (CallMeBot):** siga **https://www.callmebot.com/blog/free-api-whatsapp-messages/** (salvar o número
  deles e mandar a mensagem de ativação). A chave chega no WhatsApp → 🔑 **`CALLMEBOT_API_KEY`**. O
  **`ALERT_WHATSAPP_PHONE`** é o seu número com `+55` e DDD, só dígitos depois do `+` (ex.: `+5511999998888`).

Os dois são opcionais: sem eles o monitor funciona, e os avisos daquele canal ficam como "não configurado".

## Parte 4 — Monitor no Render (5 min + espera)

O repositório tem um [`render.yaml`](render.yaml) com toda a configuração (plano gratuito, Docker, verificação
de saúde, deploy só depois do CI). Você só preenche os segredos.

1. Entre em **https://dashboard.render.com** com a conta do GitHub.
2. Clique em **New** → **Blueprint**.
3. Escolha o repositório **maptiler-usage-monitor** e clique em **Connect**.
   - Se ele não aparecer: **Configure account** e dê permissão ao Render para ver esse repositório.
4. **Blueprint Name:** `maptiler-usage-monitor`. **Branch:** `main`.
5. O Render lista o serviço e pede os segredos. Preencha (sem espaços antes ou depois):

   | Variável | Valor |
   | --- | --- |
   | `MAPTILER_SERVICE_TOKEN` | da Parte 2 |
   | `MONGO_URL` | da Parte 1 |
   | `RESEND_API_KEY` | da Parte 3 (ou em branco) |
   | `ALERT_EMAIL_TO` | da Parte 3 (ou em branco) |
   | `ALERT_WHATSAPP_PHONE` | da Parte 3 (ou em branco) |
   | `CALLMEBOT_API_KEY` | da Parte 3 (ou em branco) |

   `CHECK_TOKEN` e `ADMIN_TOKEN` não aparecem: o Render gera os dois sozinho. Um canal que não for usar agora
   pode ficar em branco e ser preenchido depois, em **Environment**.
6. Clique em **Deploy Blueprint** e espere o status **Live** (5 a 15 minutos no primeiro deploy).
7. No topo da página do serviço está o endereço, algo como `https://maptiler-usage-monitor.onrender.com`. Abra
   **`/health`** nele: deve aparecer `{"status":"ok", ...}` com o commit em `commit`.

> Os segredos só são pedidos nesta criação. Para trocar um depois: serviço → **Environment** → editar →
> **Save Changes** (o Render reinicia o monitor).

## Parte 5 — Conferir pelo painel (3 min)

1. No Render: serviço → **Environment** → ao lado de **`ADMIN_TOKEN`**, clique no olho para ver o valor e
   🔑 copie.
2. Abra **`https://SEU-MONITOR.onrender.com/panel`**, cole o `ADMIN_TOKEN` e clique em **Entrar**.
3. Clique em **Verificar agora**. Em alguns segundos aparecem o consumo do mês, a projeção e o gráfico.
   - Erro "credencial recusada": confira o `MAPTILER_SERVICE_TOKEN`.
   - Erro de banco: confira o `MONGO_URL` (usuário, senha, nome do cluster).
4. Clique em **Testar avisos**: o e-mail chega em segundos; o WhatsApp pode levar de 15 a 20 minutos.

## Parte 6 — Ligar o agendamento no GitHub (3 min)

1. No Render, copie o valor de **`CHECK_TOKEN`** (como o `ADMIN_TOKEN` na Parte 5).
2. No GitHub: repositório **maptiler-usage-monitor** → **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**. Crie dois:

   | Nome | Valor |
   | --- | --- |
   | `MONITOR_URL` | o endereço do monitor, com `https://` e sem barra no fim |
   | `MONITOR_CHECK_TOKEN` | o `CHECK_TOKEN` do Render |

3. Aba **Actions** → **Scheduler** → **Run workflow** → em *tasks* escolha **check report** → **Run workflow**.
4. Em um ou dois minutos a execução deve ficar verde, com o resumo "check: ok" e "report: ok". O relatório chega
   no e-mail.

Pronto: a partir daí o GitHub chama o monitor às 3h17, 9h17 (com o relatório), 15h17 e 21h17, horário de
Brasília. Se uma execução falhar, chega um e-mail do GitHub.

## Parte 7 — Projetos clientes (opcional, depois)

Só é preciso quando um projeto (como o HydrantRouteSP) for ler o consumo pela API. O plano gratuito do Render
não tem terminal, então o comando roda no seu computador, apontando para o mesmo banco:

```bash
pip install -r requirements.txt
MONGO_URL='o MONGO_URL da Parte 1' DB_NAME=maptiler_monitor \
  python -m scripts.clients create HydrantRouteSP --maptiler-key <chave pública do mapa>
```

A chave do projeto aparece uma única vez: 🔑 guarde e configure no projeto cliente.

## Bom saber

- **O plano gratuito "dorme"** depois de 15 minutos sem acesso e leva cerca de 1 minuto para acordar. O
  agendamento espera ele acordar antes de chamar as rotinas; o painel pode demorar na primeira abertura.
- **Horas gratuitas:** o Render dá 750 horas por mês por conta, somando todos os serviços gratuitos (o monitor e o
  HydrantRouteSP). Serviço dormindo não gasta. O monitor fica acordado perto de 1 hora por dia, então sobra
  bastante; só os dois acordados o mês inteiro passariam do limite, e aí o Render suspende os serviços gratuitos
  até o mês seguinte.
- **Atualizações:** todo merge na `main` que mexe no monitor (pastas `app/` e `scripts/`, `Dockerfile` ou
  `requirements.txt`) publica sozinho, depois que o CI passa.
- **Trocar um token** (por exemplo, se aparecer num print): gere um novo com
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`, troque no Render (*Environment*) e, se for o
  `CHECK_TOKEN`, também no segredo `MONITOR_CHECK_TOKEN` do GitHub.
- **Agendamento pausado:** em repositório público, o GitHub pausa os agendamentos depois de 60 dias sem atividade
  no repositório e avisa por e-mail. Para religar: aba **Actions** → **Scheduler** → **Enable workflow**.

## Se algo der errado

| Sintoma | Causa provável |
| --- | --- |
| Deploy falha com erro de configuração no log (`... precisa ter pelo menos 32 caracteres`) | `CHECK_TOKEN` ou `ADMIN_TOKEN` editado à mão com menos de 32 caracteres |
| `/health` responde, mas o painel diz "banco de dados indisponível" | `MONGO_URL` errado, ou o usuário não tem *readWrite* em `maptiler_monitor` |
| "Verificar agora" falha com "credencial recusada" | `MAPTILER_SERVICE_TOKEN` errado ou de outra conta |
| Agendamento verde, mas com o aviso "agendamento desligado" | Faltam os segredos `MONITOR_URL` e `MONITOR_CHECK_TOKEN` no GitHub |
| Agendamento vermelho com "o monitor recusou o token" | `MONITOR_CHECK_TOKEN` diferente do `CHECK_TOKEN` do Render |
| Agendamento vermelho com "MONITOR_URL precisa começar com https://" | Endereço sem `https://` ou com caminho no fim |
| E-mail de teste não chega | `ALERT_EMAIL_TO` diferente do e-mail da conta do Resend (sem domínio próprio, o Resend só entrega nele) |
