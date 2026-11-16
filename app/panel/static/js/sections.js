// One render function per panel section. Each one receives its container and the API data,
// and replaces the container's content. A container's `data-labelledby` names the heading of its table.

import { badge, dataTable, emptyState, fill, h } from "./dom.js";
import { formatDate, formatDateTime, formatInteger, formatPercent, formatTimeAgo, toneForPercent } from "./format.js";
import {
  ALERT_STATUSES,
  ALERT_THRESHOLDS,
  CHANNELS,
  ERROR_KINDS,
  LEVELS,
  TRIGGERS,
  statusFor,
  textFor,
} from "./labels.js";

const FEW_DAYS = 5; // below this, the projection still swings a lot

const byRequestsDesc = (a, b) => b.requests - a.requests;

/** Requests and sessions as counts: the last two columns of the keys, services and daily tables. */
const COUNT_COLUMNS = Object.freeze([
  { title: "Requests", numeric: true, value: (row) => formatInteger(row.requests) },
  { title: "Sessions", numeric: true, value: (row) => formatInteger(row.sessions) },
]);

function tableOptions(container, emptyText) {
  return { emptyText, labelledBy: container.dataset.labelledby ?? null };
}

// ---------- status ----------

function lastErrorBox(lastError) {
  if (!lastError) return null;
  // No role="alert": this box is part of the report and is redrawn on every refresh; an alert role
  // would make screen readers announce it again each time.
  return h(
    "div",
    { class: "callout callout--critical" },
    h("strong", { text: `A verificação de ${formatDateTime(lastError.checked_at)} falhou` }),
    ` (${textFor(ERROR_KINDS, lastError.kind)}): ${lastError.message}`,
    h("p", { class: "hint", text: "Os números mostrados são os da última verificação bem-sucedida." }),
  );
}

export function renderStatus(container, usage) {
  if (usage.status === "unavailable") {
    fill(
      container,
      h("div", { class: "status__badges" }, badge("Sem dados", "critical")),
      h("p", { text: "Nenhuma verificação deu certo até agora. Use “Verificar agora”." }),
      lastErrorBox(usage.last_error),
    );
    return;
  }
  const level = statusFor(LEVELS, usage.level);
  const { period, projection } = usage;
  fill(
    container,
    h(
      "div",
      { class: "status__badges" },
      badge(level.label, level.tone),
      usage.status === "stale" ? badge("Desatualizado", "notice") : badge("Dados recentes", "ok"),
    ),
    h(
      "dl",
      { class: "facts" },
      h(
        "div",
        {},
        h("dt", { text: "Período de cobrança" }),
        h("dd", { text: `${formatDate(period.since)} a ${formatDate(period.until)}` }),
      ),
      h(
        "div",
        {},
        h("dt", { text: "Dia do período" }),
        h("dd", { text: `${projection.based_on_days} de ${projection.days_in_period}` }),
      ),
      h(
        "div",
        {},
        h("dt", { text: "Consumo obtido em" }),
        h("dd", { text: `${formatDateTime(usage.checked_at)} (${formatTimeAgo(usage.checked_at)})` }),
      ),
    ),
    h("p", {
      class: "hint",
      text: "A Analytics API do MapTiler trabalha com dados agregados: não é um medidor em tempo real.",
    }),
    projection.based_on_days < FEW_DAYS
      ? h("p", { class: "hint", text: "Com poucos dias de período, a projeção ainda oscila bastante." })
      : null,
    lastErrorBox(usage.last_error),
  );
}

// ---------- meters ----------

function meterBar(title, metric, projectedPercent) {
  const fillBar = h("div", { class: `meter__fill meter__fill--${toneForPercent(metric.percent)}` });
  fillBar.style.width = `${Math.min(metric.percent, 100)}%`;

  const marks = ALERT_THRESHOLDS.map((threshold) => {
    const mark = h(
      "div",
      { class: "meter__mark", "data-threshold": threshold },
      h("span", { class: "meter__mark-label", text: `${threshold}%` }),
    );
    mark.style.left = `${threshold}%`;
    return mark;
  });

  const projection = h("div", { class: "meter__projection", title: `Projeção: ${formatPercent(projectedPercent)}` });
  projection.style.left = `${Math.min(projectedPercent, 100)}%`;

  return h(
    "div",
    {
      class: "meter__bar",
      role: "meter",
      "aria-label": title,
      "aria-valuemin": 0,
      "aria-valuemax": 100,
      "aria-valuenow": Math.min(metric.percent, 100),
      // the text a screen reader says; also covers usage above 100%, which aria-valuenow can't show
      "aria-valuetext": `${formatPercent(metric.percent)} da cota`,
    },
    fillBar,
    marks,
    projection,
  );
}

function meter(title, metric, projectedValue, projectedPercent) {
  const overQuota = projectedPercent >= 100 ? " — passa da cota se o ritmo continuar" : "";
  return h(
    "article",
    { class: "meter" },
    h(
      "header",
      { class: "meter__header" },
      h("h3", { class: "meter__title", text: title }),
      badge(formatPercent(metric.percent), toneForPercent(metric.percent)),
    ),
    h(
      "p",
      { class: "meter__value" },
      formatInteger(metric.used),
      h("span", { class: "meter__quota", text: ` / ${formatInteger(metric.quota)}` }),
    ),
    meterBar(title, metric, projectedPercent),
    h("p", {
      class: "hint",
      text:
        `Projeção até o fim do período (tracejado): ${formatInteger(projectedValue)} ` +
        `(${formatPercent(projectedPercent)})${overQuota}`,
    }),
  );
}

export function renderMeters(container, usage) {
  if (usage.status === "unavailable") {
    fill(container, emptyState("Sem dados de consumo."));
    return;
  }
  const p = usage.projection;
  fill(
    container,
    meter("Requests", usage.requests, p.requests, p.requests_percent),
    meter("Sessions", usage.sessions, p.sessions, p.sessions_percent),
  );
}

/**
 * The chosen project's share of the account quota, above the account meters. Hidden for the whole account.
 * The meters below stay the account's: the quota, the alerts and client cut-offs belong to the account.
 */
export function renderProjectShare(container, usage) {
  const project = usage.status === "unavailable" ? null : usage.project;
  container.hidden = !project;
  if (!project) {
    container.replaceChildren();
    return;
  }
  const share = (title, metric) =>
    h(
      "div",
      { class: "project-share__item" },
      h("dt", { text: title }),
      h(
        "dd",
        {},
        h("strong", { text: formatInteger(metric.used) }),
        ` · ${formatPercent(metric.percent_of_quota)} da cota da conta`,
      ),
    );
  fill(
    container,
    h(
      "div",
      { class: "project-share" },
      h("h3", { class: "project-share__title", text: project.name }),
      h(
        "dl",
        { class: "project-share__facts" },
        share("Requests", project.requests),
        share("Sessions", project.sessions),
      ),
      h("p", {
        class: "hint",
        text: "Abaixo, o total de todos os projetos: é ele que tem cota, dispara os avisos e o corte dos projetos.",
      }),
    ),
  );
}

// ---------- breakdowns ----------

function keyName(key) {
  if (!key.name) return key.key;
  return h("span", {}, key.name, h("span", { class: "muted", text: ` · ${key.key}` }));
}

export function renderKeys(container, usage) {
  fill(
    container,
    dataTable(
      [{ title: "Chave", value: keyName }, ...COUNT_COLUMNS],
      [...usage.keys].sort(byRequestsDesc),
      tableOptions(container, "Nenhuma chave com consumo neste período."),
    ),
  );
}

export function renderServices(container, usage) {
  fill(
    container,
    dataTable(
      [{ title: "Serviço", value: (svc) => svc.label || svc.service }, ...COUNT_COLUMNS],
      [...usage.services].sort(byRequestsDesc),
      tableOptions(container, "Nenhum serviço com consumo neste período."),
    ),
  );
}

// ---------- alerts and checks ----------

function describeAlert(alert) {
  switch (alert.kind) {
    case "monitor_down":
      return "Monitor sem dados";
    case "test":
      return "Teste de aviso";
    case "report":
      return "Relatório diário";
    default:
      return `${alert.metric === "sessions" ? "Sessions" : "Requests"} em ${alert.threshold}%`;
  }
}

function alertStatusBadge(alert) {
  const { label, tone } = statusFor(ALERT_STATUSES, alert.status);
  return badge(alert.attempts > 1 ? `${label} (${alert.attempts} tentativas)` : label, tone);
}

function alertDetail(alert) {
  if (alert.last_error) return alert.last_error;
  return alert.sent_at ? `enviado em ${formatDateTime(alert.sent_at)}` : "—";
}

export function renderAlerts(container, alerts) {
  fill(
    container,
    dataTable(
      [
        { title: "Quando", value: (a) => formatDateTime(a.created_at) },
        { title: "Aviso", value: describeAlert },
        {
          title: "Consumo",
          numeric: true,
          value: (a) => (a.percent == null ? "—" : `${formatPercent(a.percent)} (${formatInteger(a.used)})`),
        },
        { title: "Canal", value: (a) => textFor(CHANNELS, a.channel) },
        { title: "Situação", value: alertStatusBadge },
        { title: "Detalhe", value: alertDetail },
      ],
      alerts,
      tableOptions(container, "Nenhum aviso até agora."),
    ),
  );
}

function describeCheck(check) {
  if (check.error) return `${textFor(ERROR_KINDS, check.error.kind)}: ${check.error.message}`;
  if (check.period_changed) return "início de novo período";
  if (check.notifications.length) return `${check.notifications.length} aviso(s)`;
  return "";
}

export function renderChecks(container, checks) {
  fill(
    container,
    dataTable(
      [
        { title: "Quando", value: (c) => formatDateTime(c.checked_at) },
        { title: "Resultado", value: (c) => (c.status === "ok" ? badge("ok", "ok") : badge("falhou", "critical")) },
        { title: "Origem", value: (c) => textFor(TRIGGERS, c.trigger) },
        { title: "Requests", numeric: true, value: (c) => formatPercent(c.requests?.percent) },
        { title: "Sessions", numeric: true, value: (c) => formatPercent(c.sessions?.percent) },
        { title: "Duração", numeric: true, value: (c) => `${formatInteger(c.duration_ms)} ms` },
        { title: "Detalhe", value: describeCheck },
      ],
      checks,
      tableOptions(container, "Nenhuma verificação registrada."),
    ),
  );
}

export function renderDaily(container, daily) {
  const newestFirst = [...daily].sort((a, b) => b.date.localeCompare(a.date));
  fill(
    container,
    dataTable(
      [{ title: "Dia", value: (d) => formatDate(d.date) }, ...COUNT_COLUMNS],
      newestFirst,
      tableOptions(container, "Sem histórico dia a dia."),
    ),
  );
}
