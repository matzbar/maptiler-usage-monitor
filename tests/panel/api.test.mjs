import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, createApiClient } from "../../app/panel/static/js/api.js";
import { createProjectStore, createTokenStore } from "../../app/panel/static/js/token-store.js";

function fakeFetch(status, body) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return new Response(body === undefined ? "" : JSON.stringify(body), { status });
  };
  return { impl, calls };
}

test("sends the bearer token and returns the JSON body", async () => {
  const { impl, calls } = fakeFetch(200, { status: "ok" });
  const api = createApiClient({ getToken: () => "segredo", fetchImpl: impl });
  assert.deepEqual(await api.usage(), { status: "ok" });
  assert.equal(calls[0].url, "/v1/usage");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.headers.get("Authorization"), "Bearer segredo");
  assert.equal(calls[0].init.cache, "no-store");
});

test("query parameters are encoded", async () => {
  const { impl, calls } = fakeFetch(200, {});
  const api = createApiClient({ getToken: () => "t", fetchImpl: impl });
  await api.history(62);
  await api.checks("1&x=2");
  assert.equal(calls[0].url, "/v1/usage/history?days=62");
  assert.equal(calls[1].url, "/v1/checks?limit=1%26x%3D2");
});

test("usage takes an optional project, encoded; clients has its own route", async () => {
  const { impl, calls } = fakeFetch(200, {});
  const api = createApiClient({ getToken: () => "t", fetchImpl: impl });
  await api.usage("");
  await api.usage("Meu projeto & cia");
  await api.clients();
  assert.deepEqual(
    calls.map((c) => c.url),
    ["/v1/usage", "/v1/usage?client=Meu%20projeto%20%26%20cia", "/v1/clients"],
  );
});

test("HTTP errors become ApiError with the API's detail", async () => {
  const { impl } = fakeFetch(429, { detail: "espere", retry_after_s: 300 });
  const api = createApiClient({ getToken: () => "t", fetchImpl: impl });
  await assert.rejects(api.runCheck(), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 429);
    assert.equal(error.message, "espere");
    assert.equal(error.body.retry_after_s, 300);
    assert.ok(error.isRateLimited && !error.isUnauthorized);
    return true;
  });
});

test("error without a JSON body still has a message", async () => {
  const { impl } = fakeFetch(503);
  const api = createApiClient({ getToken: () => "t", fetchImpl: impl });
  await assert.rejects(api.alerts(10), { status: 503, message: "erro HTTP 503" });
});

test("network failure and timeout become status 0", async () => {
  const offline = createApiClient({
    getToken: () => "t",
    fetchImpl: async () => {
      throw new TypeError("fetch failed");
    },
  });
  await assert.rejects(offline.usage(), { status: 0, message: "sem conexão com o monitor" });

  const slow = createApiClient({
    getToken: () => "t",
    timeoutMs: 10,
    fetchImpl: (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(init.signal.reason));
      }),
  });
  await assert.rejects(slow.usage(), { status: 0, message: "o monitor demorou demais para responder" });
});

test("a token that can't go in a header is a bug, not a network failure", async () => {
  const { impl, calls } = fakeFetch(200, {});
  const api = createApiClient({ getToken: () => "token€", fetchImpl: impl });
  await assert.rejects(api.usage(), (error) => !(error instanceof ApiError) && error instanceof TypeError);
  assert.equal(calls.length, 0);
});

test("token store uses the given storage and survives a broken one", () => {
  const map = new Map();
  const storage = {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  };
  const store = createTokenStore(storage);
  assert.equal(store.get(), "");
  store.set("abc");
  assert.equal(store.get(), "abc");
  store.clear();
  assert.equal(store.get(), "");

  const broken = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
    removeItem() {
      throw new Error("blocked");
    },
  };
  const fallback = createTokenStore(broken);
  fallback.set("xyz");
  assert.equal(fallback.get(), "xyz");
  fallback.clear();
  assert.equal(fallback.get(), "");
});

test("project store remembers the choice, forgets the whole account and survives a broken storage", () => {
  const map = new Map();
  const storage = {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  };
  const store = createProjectStore(storage);
  assert.equal(store.get(), "");
  store.set("HydrantRouteSP");
  assert.equal(store.get(), "HydrantRouteSP");
  store.set("");
  assert.equal(store.get(), "");
  assert.equal(map.size, 0);

  const broken = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
    removeItem() {
      throw new Error("blocked");
    },
  };
  const fallback = createProjectStore(broken);
  fallback.set("HydrantRouteSP");
  assert.equal(fallback.get(), "");
});
