// Messages shown after the panel's actions ("Verificar agora", "Testar avisos"). Pure functions: each one
// turns an API answer or an ApiError into {text, tone} for the notice above the cards.

import { formatPercent } from "./format.js";
import { CHANNELS, ERROR_KINDS, TEST_STATUSES, textFor } from "./labels.js";

const WHATSAPP_DELAY_NOTE = " O WhatsApp pode levar até 20 min para chegar.";

/** After POST /v1/checks succeeded. */
export function describeCheckSuccess(check) {
  return {
    text:
      `Verificação concluída: requests em ${formatPercent(check.requests.percent)}, ` +
      `sessions em ${formatPercent(check.sessions.percent)}.`,
    tone: "ok",
  };
}

/** After POST /v1/checks failed (429 too soon, 502 MapTiler failure, anything else). */
export function describeCheckError(error) {
  const body = error.body;
  if (error.isRateLimited && body?.retry_after_s != null) {
    const minutes = Math.ceil(body.retry_after_s / 60);
    return { text: `A última verificação é recente. Dá para verificar de novo em ${minutes} min.`, tone: "neutral" };
  }
  if (error.status === 502 && body?.error) {
    return {
      text:
        `A consulta ao MapTiler falhou (${textFor(ERROR_KINDS, body.error.kind)}): ${body.error.message}. ` +
        "A falha ficou registrada.",
      tone: "critical",
    };
  }
  return { text: `Não foi possível verificar: ${error.message}.`, tone: "critical" };
}

/** After POST /v1/alerts/test answered: one part per channel. */
export function describeTestResults(results) {
  const parts = results.map(
    (r) => `${textFor(CHANNELS, r.channel)}: ${textFor(TEST_STATUSES, r.status)}${r.error ? ` (${r.error})` : ""}`,
  );
  const whatsappSent = results.some((r) => r.channel === "whatsapp" && r.status === "sent");
  const anyFailed = results.some((r) => r.status === "failed");
  return {
    text: `Teste de aviso — ${parts.join("; ")}.${whatsappSent ? WHATSAPP_DELAY_NOTE : ""}`,
    tone: anyFailed ? "critical" : "ok",
  };
}

/** After POST /v1/alerts/test failed. */
export function describeTestError(error) {
  return { text: `Não foi possível enviar o teste: ${error.message}.`, tone: "critical" };
}
