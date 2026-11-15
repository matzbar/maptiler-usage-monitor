import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../../app/panel/static/js/api.js";
import {
  describeCheckError,
  describeCheckSuccess,
  describeTestError,
  describeTestResults,
} from "../../app/panel/static/js/messages.js";

test("successful check shows both percentages", () => {
  assert.deepEqual(describeCheckSuccess({ requests: { percent: 78.6 }, sessions: { percent: 8.4 } }), {
    text: "Verificação concluída: requests em 78,6%, sessions em 8,4%.",
    tone: "ok",
  });
});

test("check too soon says when to try again, rounding minutes up", () => {
  const error = new ApiError(429, { detail: "espere", retry_after_s: 541 });
  assert.deepEqual(describeCheckError(error), {
    text: "A última verificação é recente. Dá para verificar de novo em 10 min.",
    tone: "neutral",
  });
});

test("MapTiler failure names the kind of error and says it was recorded", () => {
  const error = new ApiError(502, {
    status: "failed",
    error: { kind: "unauthorized", message: "credencial recusada" },
  });
  assert.deepEqual(describeCheckError(error), {
    text: "A consulta ao MapTiler falhou (credencial recusada): credencial recusada. A falha ficou registrada.",
    tone: "critical",
  });
});

test("other check errors use the API's detail", () => {
  assert.deepEqual(describeCheckError(new ApiError(409, { detail: "já há uma verificação em andamento" })), {
    text: "Não foi possível verificar: já há uma verificação em andamento.",
    tone: "critical",
  });
  // 429 without retry_after_s (the per-IP limit) is not the "too soon" message
  assert.equal(describeCheckError(new ApiError(429, { detail: "muitas requisições" })).tone, "critical");
});

test("test alert results list each channel; WhatsApp sent adds the delay note", () => {
  const results = [
    { channel: "email", status: "sent", error: null },
    { channel: "whatsapp", status: "sent", error: null },
  ];
  assert.deepEqual(describeTestResults(results), {
    text: "Teste de aviso — e-mail: enviado; WhatsApp: enviado. O WhatsApp pode levar até 20 min para chegar.",
    tone: "ok",
  });
});

test("any failed channel makes the test result critical and shows its error", () => {
  const results = [
    { channel: "email", status: "failed", error: "Resend fora do ar" },
    { channel: "whatsapp", status: "not_configured", error: null },
  ];
  assert.deepEqual(describeTestResults(results), {
    text: "Teste de aviso — e-mail: falhou (Resend fora do ar); WhatsApp: não configurado.",
    tone: "critical",
  });
});

test("test alert request failure", () => {
  assert.deepEqual(describeTestError(new ApiError(0, null, "sem conexão com o monitor")), {
    text: "Não foi possível enviar o teste: sem conexão com o monitor.",
    tone: "critical",
  });
});
