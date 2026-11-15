import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { ApiError, createApiClient, isSilentRequestError, requestErrorMessage, requestMessage } from "../frontend/core/api-client.js";

const json = (payload, init = {}) => new Response(JSON.stringify(payload), { headers: { "content-type": "application/json", "x-request-id": "req-test" }, ...init });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("JSON objects serialize while FormData and binary bodies preserve their contracts", async () => {
  const sent = [];
  const client = createApiClient({ fetchImpl: async (url, init) => { sent.push([url, init]); return json({ ok: true }); } });
  await client.post("/json", { name: "商店", enabled: false });
  assert.equal(sent[0][1].body, '{"name":"商店","enabled":false}');
  assert.equal(sent[0][1].headers.get("content-type"), "application/json");
  assert.equal(sent[0][1].credentials, "same-origin");
  const form = new FormData(); form.append("file", new Blob(["a: b"]), "source.yaml");
  await client.post("/upload", form);
  assert.equal(sent[1][1].body, form);
  assert.equal(sent[1][1].headers.has("content-type"), false);
  const bytes = new Uint8Array([0, 255, 34]);
  await client.put("/binary", bytes);
  assert.equal(sent[2][1].body, bytes);
  assert.equal(sent[2][1].headers.has("content-type"), false);
});

test("bare knowledge lists, business envelopes, 204 and binary downloads stay compatible", async () => {
  const responses = [json([{ id: 1 }]), json({ ok: true, team: {} }), new Response(null, { status: 204 }),
    new Response(new Uint8Array([0, 255]), { headers: { "content-type": "application/octet-stream" } }),
    new Response("yaml: source", { headers: { "content-type": "text/yaml" } }), json({ ok: true })];
  const client = createApiClient({ fetchImpl: async () => responses.shift() });
  assert.deepEqual(await client.get("/list", { responseType: "json" }), [{ id: 1 }]);
  assert.equal((await client.get("/team", { requireOk: true })).ok, true);
  assert.equal(await client.delete("/record"), null);
  assert.deepEqual([...new Uint8Array(await (await client.get("/file")).arrayBuffer())], [0, 255]);
  assert.equal(await client.get("/yaml", { responseType: "text" }), "yaml: source");
  assert.equal((await client.get("/response", { responseType: "response" })).bodyUsed, false);
});

test("HTTP and business errors carry stable metadata, including JSON errors on binary endpoints", async () => {
  const client = createApiClient({ fetchImpl: async () => json({ ok: false, error: { code: "REVIEW_BLOCKED", message: "Missing evidence" } }) });
  for (const responseType of ["json", "blob", "arrayBuffer", "response"]) {
    await assert.rejects(client.get("/download", { responseType }), error => error instanceof ApiError && error.kind === "business"
      && error.code === "REVIEW_BLOCKED" && error.status === 200 && error.requestId === "req-test" && error.message === "Missing evidence");
  }
  const unavailable = createApiClient({ fetchImpl: async () => json({ error: "Unavailable", error_code: "DEPENDENCY_OFFLINE" }, { status: 503 }) });
  await assert.rejects(unavailable.post("/write", {}), error => error.code === "DEPENDENCY_OFFLINE" && error.status === 503 && !error.retryable && error.requestId === "req-test");
  await assert.rejects(unavailable.get("/read"), error => error.retryable && error.kind === "http");
});

test("strict envelopes and malformed success responses reject without concealing HTTP status", async () => {
  let response = json({ teams: [] });
  const client = createApiClient({ fetchImpl: async () => response });
  await assert.rejects(client.get("/team", { requireOk: true }), error => error.code === "BUSINESS_ERROR");
  response = new Response("{", { headers: { "content-type": "application/json", "x-request-id": "bad-json" } });
  await assert.rejects(client.get("/invalid"), error => error.code === "INVALID_RESPONSE" && error.status === 200 && error.requestId === "bad-json");
  response = new Response("{", { status: 502, headers: { "content-type": "application/json" } });
  await assert.rejects(client.get("/gateway"), error => error.code === "HTTP_502" && error.status === 502);
});

test("timeout covers response-body parsing and never retries a write", async () => {
  let calls = 0;
  const client = createApiClient({ timeoutMs: 15, fetchImpl: async () => {
    calls += 1;
    return { ok: true, status: 200, headers: new Headers({ "x-request-id": "slow-body", "content-type": "application/json" }), json: () => new Promise(() => {}) };
  } });
  await assert.rejects(client.post("/confirm", { records: [] }), error => error.code === "REQUEST_TIMEOUT"
    && error.status === 200 && error.requestId === "slow-body" && error.outcomeUnknown && !error.retryable
    && requestErrorMessage(error).includes(requestMessage("unknown")));
  assert.equal(calls, 1);
});

test("abort rejects even when fetch ignores its signal; pre-abort sends nothing", async () => {
  let calls = 0;
  const pending = deferred();
  const client = createApiClient({ fetchImpl: () => { calls += 1; return pending.promise; } });
  const stopped = new AbortController(); stopped.abort();
  await assert.rejects(client.get("/never", { signal: stopped.signal }), error => isSilentRequestError(error) && error.status === 0);
  assert.equal(calls, 0);
  const controller = new AbortController();
  const reading = client.get("/slow", { signal: controller.signal });
  controller.abort();
  await assert.rejects(reading, error => error.code === "REQUEST_ABORTED" && !error.outcomeUnknown);
  pending.resolve(json({ ok: true }));
  assert.equal(calls, 1);
});

test("cloud auth expiry is distinguished from a missing data session and ignores late aborted auth", async () => {
  const expired = [];
  let response = json({ error: "Session not found" }, { status: 401 });
  const client = createApiClient({ fetchImpl: async () => response, onSessionExpired: error => expired.push(error) });
  await assert.rejects(client.get("/data-session"), error => error.code === "HTTP_401");
  assert.equal(expired.length, 0);
  response = json({ error: "Sign in", needs_auth: true, code: "AUTH_REQUIRED", request_id: "body-id" }, { status: 401 });
  await assert.rejects(client.get("/private"), error => error.code === "AUTH_REQUIRED" && error.message === requestMessage("auth"));
  assert.equal(expired.length, 1);
  response = json({ ok: false, error: "Expired", code: "SESSION_EXPIRED" });
  await assert.rejects(client.get("/expired-envelope"), error => error.code === "SESSION_EXPIRED" && error.kind === "business");
  assert.equal(expired.length, 2);
  const late = deferred();
  const lateClient = createApiClient({ fetchImpl: () => late.promise, onSessionExpired: error => expired.push(error) });
  const controller = new AbortController();
  const reading = lateClient.get("/late-auth", { signal: controller.signal });
  controller.abort();
  await assert.rejects(reading, { code: "REQUEST_ABORTED" });
  late.resolve(json({ needs_auth: true }, { status: 401 }));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(expired.length, 2);
});

test("network failure is stable, writes are not retried and invalid requests never reach fetch", async () => {
  let calls = 0;
  const client = createApiClient({ fetchImpl: async () => { calls += 1; throw new TypeError("offline"); } });
  await assert.rejects(client.post("/publish", {}), error => error.code === "NETWORK_ERROR" && error.status === 0 && error.outcomeUnknown);
  assert.equal(calls, 1);
  await assert.rejects(client.request("/bad", { body: {} }), { code: "INVALID_REQUEST" });
  await assert.rejects(client.get("/bad", { timeoutMs: -1 }), { code: "INVALID_REQUEST" });
  assert.equal(calls, 1);
});

test("request status and recovery copy follows the incumbent language setting", () => {
  globalThis.getLang = () => "en";
  assert.equal(requestMessage("retry"), "Retry");
  assert.match(requestMessage("localSave", { message: "quota" }), /local copy.*quota/);
  globalThis.getLang = () => "zh";
  assert.equal(requestMessage("cancel"), "取消");
  delete globalThis.getLang;
});

test("incumbent i18n catalog translates the static parsing state and dynamic recovery messages", async () => {
  const context = { localStorage: { getItem: () => "en", setItem() {} },
    document: { addEventListener() {}, querySelectorAll: () => [], getElementById: () => null, dispatchEvent() {} },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } } };
  context.window = context;
  runInNewContext(await readFile(new URL("../frontend/legacy/i18n.js", import.meta.url), "utf8"), context);
  assert.equal(context.t("request.parsing"), "Parsing the file…");
  assert.equal(context.t("request.retryLocal"), "Retry local save");
  assert.match(context.t("request.localSave", { message: "quota" }), /local copy.*quota/);
  context.setLang("zh");
  assert.equal(context.t("request.cancel"), "取消");
  assert.equal(context.t("request.reviewBlocked"), "质量复核待修正");
});

test("the default cloud expiry handler dispatches once and redirects once", async () => {
  const previousWindow = globalThis.window;
  const previousEvent = globalThis.CustomEvent;
  const events = [];
  const locations = [];
  globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  globalThis.window = { location: { pathname: "/", assign: value => locations.push(value) }, dispatchEvent: event => events.push(event) };
  try {
    const client = createApiClient({ fetchImpl: async () => json({ needs_auth: true }, { status: 401 }) });
    await assert.rejects(client.get("/private"), { code: "SESSION_EXPIRED" });
    await assert.rejects(client.get("/private-again"), { code: "SESSION_EXPIRED" });
    assert.deepEqual(locations, ["/login"]);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "baa:session-expired");
    assert.equal(events[0].detail.requestId, "req-test");
  } finally { globalThis.window = previousWindow; globalThis.CustomEvent = previousEvent; }
});
