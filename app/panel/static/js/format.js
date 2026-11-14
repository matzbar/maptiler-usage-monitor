// Number and date formatting (pt-BR). Pure functions: no DOM, safe to unit test in Node.

export const TIME_ZONE = "America/Sao_Paulo";
const LOCALE = "pt-BR";
const MS_PER_MINUTE = 60_000;

const integerFormat = new Intl.NumberFormat(LOCALE);
const percentFormat = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dateTimeFormat = new Intl.DateTimeFormat(LOCALE, { timeZone: TIME_ZONE, dateStyle: "short", timeStyle: "short" });

const EMPTY = "—";

export function formatInteger(value) {
  return value == null ? EMPTY : integerFormat.format(value);
}

export function formatPercent(value) {
  return value == null ? EMPTY : `${percentFormat.format(value)}%`;
}

/** ISO timestamp ("2026-11-14T20:12:54Z") → "14/11/2026, 17:12" in Brasília time. */
export function formatDateTime(iso) {
  return iso ? dateTimeFormat.format(new Date(iso)) : EMPTY;
}

/** Calendar date ("2026-11-14") → "14/11/2026". Never goes through a time zone, so it never shifts a day. */
export function formatDate(isoDate) {
  if (!isoDate) return EMPTY;
  const [year, month, day] = isoDate.split("-");
  return `${day}/${month}/${year}`;
}

/** Calendar date → "14/11". */
export function formatShortDate(isoDate) {
  const [, month, day] = isoDate.split("-");
  return `${day}/${month}`;
}

/** "há 5 min", "há 3 h", "há 2 dias". */
export function formatTimeAgo(iso, now = Date.now()) {
  const minutes = Math.round((now - new Date(iso).getTime()) / MS_PER_MINUTE);
  if (minutes < 1) return "agora há pouco";
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `há ${hours} h`;
  return `há ${Math.round(hours / 24)} dias`;
}

/** Visual tone for a quota percentage, matching the alert thresholds. */
export function toneForPercent(percent) {
  if (percent >= 100) return "critical";
  if (percent >= 80) return "warning";
  if (percent >= 50) return "notice";
  return "ok";
}
