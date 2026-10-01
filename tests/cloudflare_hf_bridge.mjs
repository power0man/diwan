import assert from "node:assert/strict";
import bridge from "../deployment/cloudflare-hf/worker.mjs";

const ORIGIN = "https://diwan.260911765.xyz";
const HF = "https://hussain091-diwan-cloud-limited.hf.space";
const TOKEN = "hf_syntheticNeverARealCredential";
const AUD = "a".repeat(64);
const CSRF = "b".repeat(64);
const env = { PUBLIC_ORIGIN: ORIGIN, ACCESS_AUD: AUD, HF_TOKEN: TOKEN };
const ctx = { access: { aud: AUD } };
let calls = [];
let reply = () => new Response("synthetic", { headers: { "Content-Type": "text/plain" } });
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options });
  return reply();
};
function request(path = "/", options = {}) {
  return new Request(ORIGIN + path, options);
}
function post(extra = {}) {
  return request("/api", { method: "POST", body: "{}", headers: {
    Origin: ORIGIN, "Content-Type": "application/json", "Content-Length": "2",
    "X-Diwan-CSRF": CSRF, "Sec-Fetch-Site": "same-origin", ...extra,
  } });
}
async function deny(req, context = ctx, bindings = env, status = 403) {
  const response = await bridge.fetch(req, bindings, context);
  assert.equal(response.status, status);
  assert.equal(calls.length, 0, "refusal must precede all network use");
}
const cases = {
  unconfigured: async () => deny(request(), ctx, { ...env, HF_TOKEN: "" }, 503),
  no_access: async () => deny(request("/", { headers: {
    "Cf-Access-Jwt-Assertion": "forged", "Cf-Access-Authenticated-User-Email": "owner@example.invalid",
    Cookie: "CF_Authorization=forged",
  } }), {}),
  wrong_audience: async () => deny(request(), { access: { aud: "c".repeat(64) } }),
  wrong_target: async () => deny(new Request("https://elsewhere.example/")),
  query: async () => deny(request("/?bootstrap=secret")),
  route: async () => deny(request("/private/file")),
  cross_site: async () => deny(request("/app.js", { headers: { "Sec-Fetch-Site": "same-site" } })),
  bad_origin: async () => deny(post({ Origin: "https://evil.example" })),
  bad_csrf: async () => deny(post({ "X-Diwan-CSRF": "not-a-token" })),
  body_headers: async () => deny(post({ "Content-Type": "text/plain" })),
  body_limit: async () => deny(request("/api", { method: "POST", body: "x".repeat(524289),
    headers: { Origin: ORIGIN, "Content-Type": "application/json", "Content-Length": "524289",
      "X-Diwan-CSRF": CSRF } })),
  body_short: async () => deny(post({ "Content-Length": "3" }), ctx, env, 502),
  body_long: async () => deny(post({ "Content-Length": "1" }), ctx, env, 502),
  private_request: async () => {
    const response = await bridge.fetch(post({ Authorization: "Bearer hostile", Cookie: "secret=cookie",
      "Cf-Access-Jwt-Assertion": "private-access-token", "X-Forwarded-Host": "evil.example",
      "X-Forwarded-For": "192.0.2.1" }), env, ctx);
    assert.equal(response.status, 200);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, HF + "/api");
    const h = calls[0].options.headers;
    assert.equal(h.get("Authorization"), "Bearer " + TOKEN);
    assert.equal(h.get("Origin"), HF);
    assert.equal(h.get("X-Diwan-CSRF"), CSRF);
    for (const name of ["Cookie", "Cf-Access-Jwt-Assertion", "X-Forwarded-Host", "X-Forwarded-For"]) {
      assert.equal(h.get(name), null, name);
    }
    assert.equal(new TextDecoder().decode(calls[0].options.body), "{}");
    assert.equal(calls[0].options.redirect, "manual");
    assert.equal(calls[0].options.cache, "no-store");
  },
  redirect: async () => {
    reply = () => new Response(null, { status: 302, headers: { Location: "https://evil.example/" + TOKEN } });
    const response = await bridge.fetch(request(), env, ctx);
    assert.equal(response.status, 502);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.redirect, "manual");
    assert.equal(response.headers.get("Location"), null);
    assert.equal((await response.text()).includes(TOKEN), false);
  },
  private_response: async () => {
    reply = () => new Response("safe", { headers: { "Content-Type": "text/html",
      "Set-Cookie": "private=" + TOKEN, "Authorization": "Bearer " + TOKEN,
      "Cache-Control": "public, max-age=600", "Location": "https://hf.invalid/" + TOKEN,
      "Access-Control-Allow-Origin": "*" } });
    const response = await bridge.fetch(request(), env, ctx);
    assert.equal(response.status, 200);
    for (const name of ["Set-Cookie", "Authorization", "Location", "Access-Control-Allow-Origin"]) {
      assert.equal(response.headers.get(name), null, name);
    }
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.match(response.headers.get("Content-Security-Policy"), /connect-src 'self'/);
    assert.equal(await response.text(), "safe");
  },
  unavailable: async () => {
    reply = () => { throw new Error(TOKEN); };
    const response = await bridge.fetch(request(), env, ctx);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error_code: "bridge_unavailable" });
  },
  streaming: async () => {
    let output;
    reply = () => new Response(new ReadableStream({ start(c) { output = c; } }));
    const response = await Promise.race([bridge.fetch(request(), env, ctx),
      new Promise((_, reject) => setTimeout(() => reject(new Error("buffered response")), 500))]);
    output.enqueue(new TextEncoder().encode("first"));
    const reader = response.body.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "first");
    output.close();
    assert.equal((await reader.read()).done, true);
  },
  navigation: async () => {
    const response = await bridge.fetch(request("/", { headers: { "Sec-Fetch-Site": "cross-site",
      "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document" } }), env, ctx);
    assert.equal(response.status, 200);
    assert.equal(calls.length, 1);
  },
};
assert.ok(Object.hasOwn(cases, process.argv[2]), "known test case required");
await cases[process.argv[2]]();
