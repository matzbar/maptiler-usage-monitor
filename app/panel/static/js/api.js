// HTTP client for the monitor's /v1 routes. Knows nothing about the page: callers decide what an error means.

const DEFAULT_TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  /**
   * @param {number} status HTTP status, or 0 when the request never got an answer
   * @param {object|null} body parsed JSON body, when there is one
   */
  constructor(status, body, message) {
    super(message ?? (typeof body?.detail === "string" ? body.detail : `erro HTTP ${status}`));
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  get isUnauthorized() {
    return this.status === 401;
  }
  get isForbidden() {
    return this.status === 403;
  }
  get isRateLimited() {
    return this.status === 429;
  }
}

/**
 * @param {{getToken: () => string, fetchImpl?: typeof fetch, timeoutMs?: number}} options
 */
export function createApiClient({
  getToken,
  fetchImpl = globalThis.fetch.bind(globalThis),
  timeoutMs = DEFAULT_TIMEOUT_MS,
}) {
  async function request(method, path) {
    // Built outside the try: a browser without AbortSignal.timeout (Safari < 16) or an invalid header value
    // is a bug to report, not a network failure.
    const init = {
      method,
      headers: new Headers({ Authorization: `Bearer ${getToken()}`, Accept: "application/json" }),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    };
    let response;
    try {
      response = await fetchImpl(path, init);
    } catch (error) {
      const timedOut = error?.name === "TimeoutError";
      throw new ApiError(0, null, timedOut ? "o monitor demorou demais para responder" : "sem conexão com o monitor");
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, body);
    return body;
  }

  return {
    /** @param {string} [client] project name (admin only); omitted = the whole account */
    usage: (client) => request("GET", client ? `/v1/usage?client=${encodeURIComponent(client)}` : "/v1/usage"),
    clients: () => request("GET", "/v1/clients"),
    history: (days) => request("GET", `/v1/usage/history?days=${encodeURIComponent(days)}`),
    alerts: (limit) => request("GET", `/v1/alerts?limit=${encodeURIComponent(limit)}`),
    checks: (limit) => request("GET", `/v1/checks?limit=${encodeURIComponent(limit)}`),
    runCheck: () => request("POST", "/v1/checks"),
    sendTestAlert: () => request("POST", "/v1/alerts/test"),
  };
}
