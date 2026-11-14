// Texts shown to the user (pt-BR) for the codes the API returns.
// Kept in one place so the UI wording can change without touching logic.

/** Visual tones understood by the CSS (badge--*, meter__fill--*, notice--*). */
export const TONES = Object.freeze(["ok", "notice", "warning", "critical", "neutral"]);

/** Usage level (UsageResponse.level) → label and tone. */
export const LEVELS = Object.freeze({
  normal: { label: "Normal", tone: "ok" },
  notice: { label: "Atenção (50%)", tone: "notice" },
  warning: { label: "Aviso (70%)", tone: "notice" },
  high: { label: "Alto (80%)", tone: "warning" },
  very_high: { label: "Muito alto (90%)", tone: "warning" },
  critical: { label: "Crítico (100%)", tone: "critical" },
});

/** Alert thresholds drawn on the meters, in % of the quota. */
export const ALERT_THRESHOLDS = Object.freeze([50, 70, 80, 90, 100]);

export const ALERT_STATUSES = Object.freeze({
  recorded: { label: "registrado", tone: "neutral" },
  sending: { label: "enviando", tone: "notice" },
  sent: { label: "enviado", tone: "ok" },
  failed: { label: "falhou", tone: "critical" },
  skipped: { label: "não enviado", tone: "neutral" },
});

export const TEST_STATUSES = Object.freeze({
  sent: "enviado",
  failed: "falhou",
  not_configured: "não configurado",
  sending: "enviando",
});

export const CHANNELS = Object.freeze({ panel: "painel", email: "e-mail", whatsapp: "WhatsApp" });

export const TRIGGERS = Object.freeze({ schedule: "agendador", manual: "manual", on_demand: "leitura" });

export const ERROR_KINDS = Object.freeze({
  not_configured: "credencial não configurada",
  unauthorized: "credencial recusada",
  rejected: "consulta recusada",
  rate_limited: "limite do MapTiler",
  unavailable: "MapTiler indisponível",
  invalid_response: "resposta inesperada",
  internal: "erro interno",
});

/** Text for a code in a table of texts; unknown codes show as-is, so a new API code never breaks the UI. */
export function textFor(table, code) {
  return Object.hasOwn(table, code) ? table[code] : String(code);
}

/** {label, tone} for a code in a table of statuses; unknown codes get a neutral tone. */
export function statusFor(table, code) {
  return Object.hasOwn(table, code) ? table[code] : { label: String(code), tone: "neutral" };
}
