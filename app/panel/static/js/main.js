// Admin panel entry point: sign-in, loading data, actions and screen state.
//
// The page is static and carries no data: everything comes from the /v1 routes using the ADMIN_TOKEN
// the person pastes on the sign-in screen (kept only in this tab, see token-store.js).
//
// Screen state lives in `state`, never in the DOM. Each sign-in starts a new session number; work started
// in an older session (a load still in flight when the person signed out) is discarded when it finishes.

import { ApiError, createApiClient } from "./api.js";
import { renderUsageChart } from "./chart.js";
import { fill, h } from "./dom.js";
import { formatDateTime } from "./format.js";
import { describeCheckError, describeCheckSuccess, describeTestError, describeTestResults } from "./messages.js";
import {
  renderAlerts,
  renderChecks,
  renderDaily,
  renderKeys,
  renderMeters,
  renderProjectShare,
  renderServices,
  renderStatus,
} from "./sections.js";
import { createProjectStore, createTokenStore } from "./token-store.js";

const HISTORY_DAYS = 62; // covers the current billing period plus the previous one
const ALERTS_LIMIT = 50;
const CHECKS_LIMIT = 20;
const RELATIVE_TIME_REFRESH_MS = 60_000; // keeps "há X min" in the status card current
const TOKEN_PATTERN = /^[\x21-\x7e]+$/; // printable ASCII: what an HTTP header can carry
const UNEXPECTED_ERROR = "Ocorreu um erro inesperado ao exibir os dados. Detalhes no console do navegador.";

const tokenStore = createTokenStore();
const projectStore = createProjectStore();
const api = createApiClient({ getToken: () => tokenStore.get() });

/** Elements the controller touches, looked up once. */
const ui = Object.fromEntries(
  [
    "login",
    "login-form",
    "token-input",
    "login-error",
    "login-submit",
    "app",
    "updated-at",
    "notice",
    "refresh-button",
    "check-button",
    "test-button",
    "logout-button",
    "project-select",
    "status",
    "project-share",
    "meters",
    "chart",
    "keys",
    "services",
    "alerts",
    "checks",
    "daily",
  ].map((id) => [id, document.getElementById(id)]),
);

const state = {
  signedIn: false,
  session: 0, // incremented on every sign-in and sign-out
  loading: null, // {session, promise} of the load in flight, so concurrent loads of a session share it
  usage: null, // last UsageResponse shown
  daily: null, // last daily history shown
  project: projectStore.get(), // chosen project name; "" = the whole account
};

const ACCOUNT_SCOPE_TAGS = document.querySelectorAll("[data-account-scope]");

// ---------- screen state ----------

/** Message above the cards. The live region itself is always in the page, so screen readers announce it. */
function showNotice(text, tone = "neutral") {
  if (!text) {
    ui.notice.replaceChildren();
    return;
  }
  fill(ui.notice, h("p", { class: `notice notice--${tone}`, text }));
}

function showApp() {
  state.signedIn = true;
  ui.login.hidden = true;
  ui.app.hidden = false;
}

function showLogin(reason = "") {
  state.signedIn = false;
  state.session += 1; // anything still running for the old session is discarded when it finishes
  state.usage = null;
  state.daily = null;
  showNotice(""); // a message from this session must not show up in the next one
  tokenStore.clear();
  ui.app.hidden = true;
  ui.login.hidden = false;
  ui["login-error"].textContent = reason;
  ui["token-input"].value = "";
  ui["token-input"].focus();
}

/** Errors that end the session. Returns true when it handled the error. */
function handleAuthError(error) {
  if (!(error instanceof ApiError)) return false;
  if (error.isUnauthorized) {
    showLogin("O token não foi aceito. Entre de novo.");
    return true;
  }
  if (error.isForbidden) {
    showLogin("Esse token não é o de administrador.");
    return true;
  }
  return false;
}

/** Last resort for bugs and unexpected API shapes: tell the person instead of failing silently. */
function reportUnexpected(error) {
  console.error("Painel: erro inesperado", error);
  if (state.signedIn) showNotice(UNEXPECTED_ERROR, "critical");
  else ui["login-error"].textContent = UNEXPECTED_ERROR;
}

async function withBusy(button, action) {
  button.disabled = true;
  try {
    return await action();
  } finally {
    button.disabled = false;
  }
}

// ---------- loading ----------

/** Project picker options: the whole account, then each project (inactive ones say so). */
function renderProjectPicker(clients) {
  const options = [h("option", { value: "", text: "Conta inteira" })];
  for (const client of clients) {
    const label = client.active ? client.name : `${client.name} (desativado)`;
    options.push(h("option", { value: client.name, text: label }));
  }
  fill(ui["project-select"], options);
  ui["project-select"].value = state.project;
}

function render(usage, history, alerts, checks) {
  renderStatus(ui.status, usage);
  renderProjectShare(ui["project-share"], usage);
  renderMeters(ui.meters, usage);
  renderUsageChart(ui.chart, usage, history.daily);
  renderKeys(ui.keys, usage);
  renderServices(ui.services, usage);
  renderAlerts(ui.alerts, alerts.alerts);
  renderChecks(ui.checks, checks.checks);
  renderDaily(ui.daily, history.daily);
  // With a project chosen, these sections still show the whole account: say so next to their titles.
  for (const tag of ACCOUNT_SCOPE_TAGS) tag.hidden = !state.project;
  ui["updated-at"].textContent = usage.checked_at ? `Consumo de ${formatDateTime(usage.checked_at)}` : "Sem dados";
  state.usage = usage;
  state.daily = history.daily;
}

async function fetchAndRender() {
  const session = state.session;
  ui["project-select"].disabled = true; // the choice can't change halfway through a load
  try {
    // Projects first: a remembered project that no longer exists falls back to the whole account.
    const { clients } = await api.clients();
    if (session !== state.session) return false;
    if (state.project && !clients.some((c) => c.name === state.project)) chooseProject("");
    renderProjectPicker(clients);
    const [usage, history, alerts, checks] = await Promise.all([
      api.usage(state.project),
      api.history(HISTORY_DAYS),
      api.alerts(ALERTS_LIMIT),
      api.checks(CHECKS_LIMIT),
    ]);
    if (session !== state.session) return false; // signed out (or in again) while loading
    showApp(); // before rendering: the chart measures its container's width
    render(usage, history, alerts, checks);
    return true;
  } catch (error) {
    if (session !== state.session) return false;
    if (handleAuthError(error)) return false;
    if (!(error instanceof ApiError)) throw error;
    if (!state.signedIn) {
      // Still on the sign-in screen: the token was not confirmed, so don't open the panel.
      showLogin(
        error.isRateLimited
          ? "Muitas tentativas seguidas. Espere um minuto e tente de novo."
          : `Não foi possível entrar: ${error.message}.`,
      );
    } else {
      showNotice(`Não foi possível carregar os dados: ${error.message}.`, "critical");
    }
    return false;
  } finally {
    ui["project-select"].disabled = false;
  }
}

function chooseProject(name) {
  state.project = name;
  projectStore.set(name);
}

/**
 * Loads everything. Never rejects. A call made while a load of the *same* session is running reuses it;
 * a load left over from an earlier session is never reused (its result is discarded), so signing in
 * again while it is still in flight starts a fresh load instead of waiting on one that will be thrown away.
 */
function load() {
  if (state.loading?.session === state.session) return state.loading.promise;
  const promise = withBusy(ui["refresh-button"], fetchAndRender)
    .catch((error) => {
      reportUnexpected(error);
      return false;
    })
    .finally(() => {
      if (state.loading?.promise === promise) state.loading = null;
    });
  state.loading = { session: state.session, promise };
  return promise;
}

// ---------- actions ----------

/**
 * Runs a button action: disables the button meanwhile, ends the session on 401/403 and shows
 * `describeError(error)` ({text, tone}) for other API errors. Unexpected errors are reported, never swallowed.
 */
async function runAction(button, action, describeError) {
  const session = state.session;
  try {
    await withBusy(button, action);
  } catch (error) {
    if (session !== state.session || handleAuthError(error)) return;
    if (error instanceof ApiError) {
      const { text, tone } = describeError(error);
      showNotice(text, tone);
    } else {
      reportUnexpected(error);
    }
  }
}

async function runCheck() {
  showNotice("Consultando o MapTiler…");
  await runAction(
    ui["check-button"],
    async () => {
      const { text, tone } = describeCheckSuccess(await api.runCheck());
      showNotice(text, tone);
    },
    describeCheckError,
  );
  if (state.signedIn) await load(); // only replaces the notice if reloading fails
}

async function sendTestAlert() {
  if (!window.confirm("Mandar uma mensagem de teste por e-mail e WhatsApp agora?")) return;
  showNotice("Enviando avisos de teste…");
  await runAction(
    ui["test-button"],
    async () => {
      const { results } = await api.sendTestAlert();
      const { text, tone } = describeTestResults(results);
      showNotice(text, tone);
    },
    describeTestError,
  );
}

async function signIn(event) {
  event.preventDefault();
  const token = ui["token-input"].value.trim();
  if (!token) return;
  if (!TOKEN_PATTERN.test(token)) {
    ui["login-error"].textContent = "O token tem caracteres inválidos (espaços ou acentos). Confira o que foi colado.";
    return;
  }
  state.session += 1;
  tokenStore.set(token);
  ui["login-error"].textContent = "";
  await withBusy(ui["login-submit"], load);
  ui["token-input"].value = "";
}

// ---------- wiring ----------

function redrawChartOnResize() {
  let frame = 0;
  let lastWidth = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const width = ui.chart.clientWidth;
      if (state.signedIn && state.usage && width !== lastWidth) {
        lastWidth = width;
        renderUsageChart(ui.chart, state.usage, state.daily);
      }
    });
  });
}

function refreshRelativeTimes() {
  window.setInterval(() => {
    if (state.signedIn && state.usage && !document.hidden) renderStatus(ui.status, state.usage);
  }, RELATIVE_TIME_REFRESH_MS);
}

function start() {
  ui["login-form"].addEventListener("submit", signIn);
  ui["refresh-button"].addEventListener("click", () => {
    showNotice("");
    load();
  });
  ui["check-button"].addEventListener("click", runCheck);
  ui["test-button"].addEventListener("click", sendTestAlert);
  ui["logout-button"].addEventListener("click", () => showLogin());
  ui["project-select"].addEventListener("change", () => {
    chooseProject(ui["project-select"].value);
    showNotice("");
    load();
  });
  redrawChartOnResize();
  refreshRelativeTimes();

  if (tokenStore.get()) load();
  else showLogin();
}

start(); // module scripts run after the document is parsed
