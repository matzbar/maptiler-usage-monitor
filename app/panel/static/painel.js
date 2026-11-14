// Painel do administrador do monitor MapTiler.
//
// Página estática: os dados vêm das rotas /v1 com o ADMIN_TOKEN que a pessoa digita. O token fica só em
// sessionStorage (some ao fechar a aba). Todo texto vindo da API entra no DOM por textContent, nunca como HTML.
"use strict";

const CHAVE_TOKEN = "monitor-maptiler-admin";
const FUSO = "America/Sao_Paulo";
const FAIXAS = [50, 70, 80, 90, 100];

const NIVEL = {
  normal: ["Normal", "ok"],
  notice: ["Atenção (50%)", "atencao"],
  warning: ["Aviso (70%)", "atencao"],
  high: ["Alto (80%)", "alerta"],
  very_high: ["Muito alto (90%)", "alerta"],
  critical: ["Crítico (100%)", "critico"],
};
const STATUS_AVISO = {
  recorded: ["registrado", ""], sending: ["enviando", "atencao"], sent: ["enviado", "ok"],
  failed: ["falhou", "critico"], skipped: ["não enviado", ""],
};
const CANAL = { panel: "painel", email: "e-mail", whatsapp: "WhatsApp" };
const GATILHO = { schedule: "agendador", manual: "manual", on_demand: "leitura" };
const ERRO_TIPO = {
  not_configured: "credencial não configurada", unauthorized: "credencial recusada", rejected: "consulta recusada",
  rate_limited: "limite do MapTiler", unavailable: "MapTiler indisponível", invalid_response: "resposta inesperada",
  internal: "erro interno",
};

const $ = (id) => document.getElementById(id);

// ---------- token ----------

function lerToken() {
  try { return sessionStorage.getItem(CHAVE_TOKEN) || ""; } catch { return memoria.token; }
}
function gravarToken(valor) {
  memoria.token = valor;
  try { valor ? sessionStorage.setItem(CHAVE_TOKEN, valor) : sessionStorage.removeItem(CHAVE_TOKEN); } catch { /* aba privada */ }
}
const memoria = { token: "" };

// ---------- API ----------

class ErroApi extends Error {
  constructor(status, corpo) {
    super((corpo && typeof corpo.detail === "string" && corpo.detail) || `erro HTTP ${status}`);
    this.status = status;
    this.corpo = corpo;
  }
}

async function api(metodo, caminho) {
  let resposta;
  try {
    resposta = await fetch(caminho, {
      method: metodo,
      headers: { Authorization: `Bearer ${lerToken()}`, Accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    throw new ErroApi(0, { detail: "sem conexão com o monitor" });
  }
  let corpo = null;
  try { corpo = await resposta.json(); } catch { /* corpo vazio */ }
  if (resposta.status === 401) {
    sair("O token não foi aceito. Entre de novo.");
    throw new ErroApi(401, corpo);
  }
  if (!resposta.ok) throw new ErroApi(resposta.status, corpo);
  return corpo;
}

// ---------- formatação ----------

const fmtInt = new Intl.NumberFormat("pt-BR");
const fmtPct = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmtDataHora = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, dateStyle: "short", timeStyle: "short" });

const inteiro = (n) => (n == null ? "—" : fmtInt.format(n));
const pct = (n) => (n == null ? "—" : `${fmtPct.format(n)}%`);
const dataHora = (iso) => (iso ? fmtDataHora.format(new Date(iso)) : "—");
// Datas sem hora ("2026-11-14") são dias do calendário: formatadas sem passar por fuso.
const dia = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");
const diaCurto = (iso) => iso.split("-").reverse().slice(0, 2).join("/");

function haQuanto(iso) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "agora há pouco";
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `há ${h} h`;
  return `há ${Math.round(h / 24)} dias`;
}

function classePorPct(p) {
  if (p >= 100) return "critico";
  if (p >= 80) return "alerta";
  if (p >= 50) return "atencao";
  return "";
}

// ---------- DOM ----------

function el(tag, props = {}, ...filhos) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f != null) node.append(f);
  return node;
}

function selo(texto, classe) {
  return el("span", { class: `selo ${classe || ""}`.trim(), text: texto });
}

function preencher(id, ...filhos) {
  $(id).replaceChildren(...filhos.flat().filter((f) => f != null));
}

function tabela(colunas, linhas, vazio) {
  if (!linhas.length) return el("p", { class: "vazio", text: vazio });
  const cab = el("tr", {}, colunas.map((c) => el("th", { class: c.num ? "num" : null, scope: "col", text: c.titulo })));
  const corpo = linhas.map((l) => el("tr", {}, colunas.map((c) => {
    const valor = c.valor(l);
    const td = el("td", { class: c.num ? "num" : null });
    td.append(valor instanceof Node ? valor : document.createTextNode(valor ?? "—"));
    return td;
  })));
  return el("div", { class: "rolagem" }, el("table", {}, el("thead", {}, cab), el("tbody", {}, corpo)));
}

function mensagem(texto, tipo) {
  const m = $("mensagem");
  m.textContent = texto;
  m.className = `mensagem ${tipo || ""}`.trim();
  m.hidden = !texto;
}

// ---------- seções ----------

function renderSituacao(uso) {
  if (uso.status === "unavailable") {
    preencher("situacao",
      el("div", { class: "linha-situacao" }, selo("Sem dados", "critico")),
      el("p", { class: "situacao-texto", text: "Nenhuma verificação deu certo até agora. Use “Verificar agora”." }),
      falha(uso.last_error));
    $("atualizado").textContent = "";
    return;
  }
  const [rotulo, classe] = NIVEL[uso.level] || [uso.level, ""];
  const p = uso.period, proj = uso.projection;
  preencher("situacao",
    el("div", { class: "linha-situacao" },
      selo(rotulo, classe),
      uso.status === "stale" ? selo("Desatualizado", "atencao") : selo("Dados recentes", "ok")),
    el("p", { class: "situacao-texto",
      text: `Período de cobrança de ${dia(p.since)} a ${dia(p.until)} — dia ${proj.based_on_days} de ${proj.days_in_period}.` }),
    el("p", { class: "nota",
      text: `Consumo obtido do MapTiler em ${dataHora(uso.checked_at)} (${haQuanto(uso.checked_at)}). ` +
            "A Analytics API trabalha com dados agregados: não é um medidor em tempo real." }),
    proj.based_on_days < 5
      ? el("p", { class: "nota", text: "Com poucos dias de período, a projeção ainda oscila bastante." })
      : null,
    falha(uso.last_error));
  $("atualizado").textContent = `Consumo de ${dataHora(uso.checked_at)}`;
}

function falha(erro) {
  if (!erro) return null;
  return el("div", { class: "falha", role: "alert" },
    el("strong", { text: `A verificação de ${dataHora(erro.checked_at)} falhou` }),
    el("span", { text: ` (${ERRO_TIPO[erro.kind] || erro.kind}): ${erro.message}` }),
    el("p", { class: "nota", text: "Os números mostrados são os da última verificação bem-sucedida." }));
}

function medidor(titulo, m, projPct, projValor) {
  const cheio = el("div", { class: `barra-cheia ${classePorPct(m.percent)}`.trim() });
  cheio.style.width = `${Math.min(m.percent, 100)}%`;
  const marcas = FAIXAS.map((f) => {
    const marca = el("div", { class: f === 100 ? "marca ultima" : f === 90 ? "marca noventa" : "marca" }, el("span", { text: `${f}%` }));
    marca.style.left = `${f}%`;
    return marca;
  });
  const projecao = el("div", { class: "projecao", title: `Projeção: ${pct(projPct)}` });
  projecao.style.left = `${Math.min(projPct, 100)}%`;
  const barra = el("div", {
    class: "barra", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100",
    "aria-valuenow": String(Math.min(m.percent, 100)), "aria-label": `${titulo}: ${pct(m.percent)} da cota`,
  }, cheio, marcas, projecao);
  const passa = projPct >= 100 ? " — passa da cota se o ritmo continuar" : "";
  return el("div", { class: "medidor" },
    el("h3", {}, el("span", { text: titulo }), selo(pct(m.percent), classePorPct(m.percent))),
    el("p", { class: "valor", text: inteiro(m.used) }),
    el("p", { class: "legenda", text: `de ${inteiro(m.quota)} no período` }),
    barra,
    el("p", { class: "legenda", text: `Projeção até o fim do período (tracejado): ${inteiro(projValor)} (${pct(projPct)})${passa}` }));
}

function renderMedidores(uso) {
  if (uso.status === "unavailable") {
    preencher("medidores", el("p", { class: "vazio", text: "Sem dados de consumo." }));
    return;
  }
  const p = uso.projection;
  preencher("medidores",
    medidor("Requests", uso.requests, p.requests_percent, p.requests),
    medidor("Sessions", uso.sessions, p.sessions_percent, p.sessions));
}

// Gráfico em SVG: requests acumulados no período, linha da cota, linha dos 80% e projeção tracejada.
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...filhos) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) node.setAttribute(k, v);
  for (const f of filhos) if (f != null) node.append(f);
  return node;
}

function renderGrafico(uso, diario) {
  if (uso.status === "unavailable") {
    preencher("grafico", el("p", { class: "vazio", text: "Sem dados de consumo." }));
    return;
  }
  const { since, until } = uso.period;
  const totalDias = uso.projection.days_in_period;
  const dias = diario.filter((d) => d.date >= since && d.date <= until).sort((a, b) => a.date.localeCompare(b.date));
  if (!dias.length) {
    preencher("grafico", el("p", { class: "vazio", text: "Ainda não há consumo dia a dia neste período." }));
    return;
  }
  const indice = (iso) => Math.round((Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${since}T00:00:00Z`)) / 864e5);
  let acumulado = 0;
  const pontos = dias.map((d) => ({ i: indice(d.date), v: (acumulado += d.requests), date: d.date }));
  const cota = uso.requests.quota;
  const projetado = uso.projection.requests;
  const topo = Math.max(cota, projetado, acumulado) * 1.08;

  // Desenhado na largura real do cartão (1 unidade = 1 px), para o texto não encolher no celular.
  const W = Math.max(Math.round($("grafico").clientWidth) || 720, 280), H = W < 520 ? 220 : 260;
  const esq = 64, dir = 16, cima = 12, baixo = 28;
  const x = (i) => esq + (i / Math.max(totalDias - 1, 1)) * (W - esq - dir);
  const y = (v) => cima + (1 - v / topo) * (H - cima - baixo);

  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": `Requests acumulados: ${inteiro(acumulado)} de ${inteiro(cota)}; projeção de ${inteiro(projetado)} até ${dia(until)}` });
  // eixo e marcas de valor, em passos "redondos" (1, 2 ou 5 × 10^n) até o topo do gráfico
  const bruto = topo / (H < 240 ? 4 : 5), base = 10 ** Math.floor(Math.log10(bruto));
  const passo = [1, 2, 5, 10].map((m) => m * base).find((s) => s >= bruto);
  for (let v = 0; v <= topo; v += passo) {
    g.append(svg("line", { class: "eixo", x1: esq, x2: W - dir, y1: y(v), y2: y(v) }));
    g.append(svg("text", { x: esq - 6, y: y(v) + 4, "text-anchor": "end" }, document.createTextNode(fmtInt.format(v))));
  }
  g.append(svg("line", { class: "oitenta", x1: esq, x2: W - dir, y1: y(cota * 0.8), y2: y(cota * 0.8) }));
  g.append(svg("line", { class: "cota", x1: esq, x2: W - dir, y1: y(cota), y2: y(cota) }));
  g.append(svg("text", { x: W - dir, y: y(cota) - 5, "text-anchor": "end" }, document.createTextNode("cota")));
  g.append(svg("text", { x: W - dir, y: y(cota * 0.8) - 5, "text-anchor": "end" }, document.createTextNode("80%")));
  // datas: início, meio e fim do período
  const meio = Math.floor((totalDias - 1) / 2);
  const isoDoIndice = (i) => new Date(Date.parse(`${since}T00:00:00Z`) + i * 864e5).toISOString().slice(0, 10);
  for (const [i, ancora] of [[0, "start"], [meio, "middle"], [totalDias - 1, "end"]]) {
    g.append(svg("text", { x: x(i), y: H - 8, "text-anchor": ancora }, document.createTextNode(diaCurto(isoDoIndice(i)))));
  }
  const ultimo = pontos[pontos.length - 1];
  if (ultimo.i < totalDias - 1) {
    g.append(svg("line", { class: "projetado", x1: x(ultimo.i), y1: y(ultimo.v), x2: x(totalDias - 1), y2: y(projetado) }));
  }
  const d = pontos.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  g.append(svg("path", { class: "uso", d }));
  for (const p of pontos) {
    const c = svg("circle", { cx: x(p.i), cy: y(p.v), r: 3, class: "ponto" });
    c.append(svg("title", {}, document.createTextNode(`${dia(p.date)}: ${inteiro(p.v)} acumulados`)));
    g.append(c);
  }
  preencher("grafico", g,
    el("p", { class: "legenda", text: "Linha contínua: consumo acumulado (do histórico dia a dia). Tracejado: projeção pela média diária. " +
      "O histórico pode ficar um pouco atrás do total do período, que vem direto da última verificação." }));
}

function renderChaves(uso) {
  preencher("chaves", tabela([
    { titulo: "Chave", valor: (k) => (k.name ? `${k.name} (${k.key})` : k.key) },
    { titulo: "Requests", num: true, valor: (k) => inteiro(k.requests) },
    { titulo: "Sessions", num: true, valor: (k) => inteiro(k.sessions) },
  ], [...uso.keys].sort((a, b) => b.requests - a.requests), "Nenhuma chave com consumo neste período."));
}

function renderServicos(uso) {
  preencher("servicos", tabela([
    { titulo: "Serviço", valor: (s) => s.label || s.service },
    { titulo: "Requests", num: true, valor: (s) => inteiro(s.requests) },
    { titulo: "Sessions", num: true, valor: (s) => inteiro(s.sessions) },
  ], [...uso.services].sort((a, b) => b.requests - a.requests), "Nenhum serviço com consumo neste período."));
}

function descricaoAviso(a) {
  if (a.kind === "monitor_down") return "Monitor sem dados";
  if (a.kind === "test") return "Teste de aviso";
  if (a.kind === "report") return "Relatório diário";
  return `${a.metric === "sessions" ? "Sessions" : "Requests"} em ${a.threshold}%`;
}

function renderAvisos(avisos) {
  preencher("avisos", tabela([
    { titulo: "Quando", valor: (a) => dataHora(a.created_at) },
    { titulo: "Aviso", valor: (a) => descricaoAviso(a) },
    { titulo: "Consumo", num: true, valor: (a) => (a.percent == null ? "—" : `${pct(a.percent)} (${inteiro(a.used)})`) },
    { titulo: "Canal", valor: (a) => CANAL[a.channel] || a.channel },
    { titulo: "Situação", valor: (a) => {
      const [t, c] = STATUS_AVISO[a.status] || [a.status, ""];
      return selo(a.attempts > 1 ? `${t} (${a.attempts} tentativas)` : t, c);
    } },
    { titulo: "Detalhe", valor: (a) => a.last_error || (a.sent_at ? `enviado em ${dataHora(a.sent_at)}` : "—") },
  ], avisos, "Nenhum aviso até agora."));
}

function renderVerificacoes(checks) {
  preencher("verificacoes", tabela([
    { titulo: "Quando", valor: (c) => dataHora(c.checked_at) },
    { titulo: "Resultado", valor: (c) => (c.status === "ok" ? selo("ok", "ok") : selo("falhou", "critico")) },
    { titulo: "Origem", valor: (c) => GATILHO[c.trigger] || c.trigger },
    { titulo: "Requests", num: true, valor: (c) => (c.requests ? pct(c.requests.percent) : "—") },
    { titulo: "Sessions", num: true, valor: (c) => (c.sessions ? pct(c.sessions.percent) : "—") },
    { titulo: "Duração", num: true, valor: (c) => `${fmtInt.format(c.duration_ms)} ms` },
    { titulo: "Detalhe", valor: (c) => (c.error ? `${ERRO_TIPO[c.error.kind] || c.error.kind}: ${c.error.message}`
      : c.period_changed ? "início de novo período" : c.notifications.length ? `${c.notifications.length} aviso(s)` : "") },
  ], checks, "Nenhuma verificação registrada."));
}

function renderDiario(diario) {
  const linhas = [...diario].sort((a, b) => b.date.localeCompare(a.date));
  preencher("diario", tabela([
    { titulo: "Dia", valor: (d) => dia(d.date) },
    { titulo: "Requests", num: true, valor: (d) => inteiro(d.requests) },
    { titulo: "Sessions", num: true, valor: (d) => inteiro(d.sessions) },
  ], linhas, "Sem histórico dia a dia."));
}

// ---------- fluxo ----------

let carregando = false;
let ultimoGrafico = null; // [uso, diário]: para redesenhar o gráfico quando a largura muda
let larguraGrafico = 0;

function redesenharAoRedimensionar() {
  let agendado = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(agendado);
    agendado = requestAnimationFrame(() => {
      const largura = $("grafico").clientWidth;
      if (ultimoGrafico && largura !== larguraGrafico) {
        larguraGrafico = largura;
        renderGrafico(...ultimoGrafico);
      }
    });
  });
}

async function carregar() {
  if (carregando) return;
  carregando = true;
  $("btn-atualizar").disabled = true;
  try {
    const [uso, historico, avisos, checks] = await Promise.all([
      api("GET", "/v1/usage"),
      api("GET", "/v1/usage/history?days=62"),
      api("GET", "/v1/alerts?limit=50"),
      api("GET", "/v1/checks?limit=20"),
    ]);
    mostrarPainel();
    renderSituacao(uso);
    renderMedidores(uso);
    renderGrafico(uso, historico.daily);
    ultimoGrafico = [uso, historico.daily];
    renderChaves(uso);
    renderServicos(uso);
    renderAvisos(avisos.alerts);
    renderVerificacoes(checks.checks);
    renderDiario(historico.daily);
    return true;
  } catch (erro) {
    if (erro.status === 401) return false;
    if (erro.status === 403) {
      sair("Esse token não é o de administrador.");
      return false;
    }
    if ($("painel").hidden) {
      // Ainda na tela de entrada: o token não foi confirmado, então não entra no painel.
      sair(erro.status === 429 ? "Muitas tentativas seguidas. Espere um minuto e tente de novo."
        : `Não foi possível entrar: ${erro.message}.`);
      return false;
    }
    mensagem(`Não foi possível carregar os dados: ${erro.message}.`, "ruim");
    return false;
  } finally {
    carregando = false;
    $("btn-atualizar").disabled = false;
  }
}

async function verificarAgora() {
  const botao = $("btn-verificar");
  botao.disabled = true;
  mensagem("Consultando o MapTiler…");
  try {
    const r = await api("POST", "/v1/checks");
    mensagem(`Verificação concluída: requests em ${pct(r.requests.percent)}, sessions em ${pct(r.sessions.percent)}.`, "boa");
  } catch (erro) {
    if (erro.status === 401) return;
    if (erro.status === 429 && erro.corpo && erro.corpo.retry_after_s != null) {
      const min = Math.ceil(erro.corpo.retry_after_s / 60);
      mensagem(`A última verificação é recente. Dá para verificar de novo em ${min} min.`, "");
    } else if (erro.status === 502 && erro.corpo && erro.corpo.error) {
      const e = erro.corpo.error;
      mensagem(`A consulta ao MapTiler falhou (${ERRO_TIPO[e.kind] || e.kind}): ${e.message}. A falha ficou registrada.`, "ruim");
    } else {
      mensagem(`Não foi possível verificar: ${erro.message}.`, "ruim");
    }
  } finally {
    botao.disabled = false;
  }
  await carregar(); // só troca a mensagem se o recarregamento falhar
}

async function testarAvisos() {
  if (!confirm("Mandar uma mensagem de teste por e-mail e WhatsApp agora?")) return;
  const botao = $("btn-teste");
  botao.disabled = true;
  mensagem("Enviando avisos de teste…");
  try {
    const r = await api("POST", "/v1/alerts/test");
    const STATUS_TESTE = { sent: "enviado", failed: "falhou", not_configured: "não configurado", sending: "enviando" };
    const partes = r.results.map((t) => `${CANAL[t.channel]}: ${STATUS_TESTE[t.status] || t.status}${t.error ? ` (${t.error})` : ""}`);
    const algumaFalha = r.results.some((t) => t.status === "failed");
    mensagem(`Teste de aviso — ${partes.join("; ")}.` +
      (r.results.some((t) => t.channel === "whatsapp" && t.status === "sent") ? " O WhatsApp pode levar até 20 min para chegar." : ""),
      algumaFalha ? "ruim" : "boa");
  } catch (erro) {
    if (erro.status !== 401) mensagem(`Não foi possível enviar o teste: ${erro.message}.`, "ruim");
  } finally {
    botao.disabled = false;
  }
}

function mostrarPainel() {
  $("entrar").hidden = true;
  $("painel").hidden = false;
  $("acoes").hidden = false;
}

function sair(motivo) {
  gravarToken("");
  $("painel").hidden = true;
  $("acoes").hidden = true;
  $("entrar").hidden = false;
  $("atualizado").textContent = "";
  $("erro-entrar").textContent = motivo || "";
  $("token").value = "";
  $("token").focus();
}

async function entrar(evento) {
  evento.preventDefault();
  const valor = $("token").value.trim();
  if (!valor) return;
  gravarToken(valor);
  $("erro-entrar").textContent = "";
  const botao = evento.submitter || $("form-entrar").querySelector("button");
  botao.disabled = true;
  await carregar();
  botao.disabled = false;
  $("token").value = "";
}

document.addEventListener("DOMContentLoaded", () => {
  $("form-entrar").addEventListener("submit", entrar);
  $("btn-atualizar").addEventListener("click", () => { mensagem(""); carregar(); });
  $("btn-verificar").addEventListener("click", verificarAgora);
  $("btn-teste").addEventListener("click", testarAvisos);
  $("btn-sair").addEventListener("click", () => sair(""));
  redesenharAoRedimensionar();
  if (lerToken()) carregar();
  else sair("");
});
