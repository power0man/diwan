// Synthetic IO only. Runs the exact source in workerd, including cache semantics
// that a Node fetch stub cannot validate. This is not a live Access acceptance.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const aud = "a".repeat(64);
const origin = "https://diwan.260911765.xyz";
const csrf = "b".repeat(64);
const base = {
  name: "diwan-bridge-smoke", modules: true,
  script: readFileSync(new URL("./worker.mjs", import.meta.url), "utf8"),
  compatibilityDate: "2026-10-01", compatibilityFlags: ["nodejs_compat"],
  bindings: { PUBLIC_ORIGIN: origin, ACCESS_AUD: aud, HF_TOKEN: "hf_syntheticOnly" },
};
let calls = 0;
const outboundService = async (request) => {
  calls++;
  assert.equal(new URL(request.url).hostname, "hussain091-diwan-cloud-limited.hf.space");
  assert.equal(request.headers.get("Authorization"), "Bearer hf_syntheticOnly");
  assert.equal(request.headers.get("Cookie"), null);
  if (request.method === "POST") {
    assert.equal(request.headers.get("Origin"), "https://hussain091-diwan-cloud-limited.hf.space");
    assert.equal(request.headers.get("X-Diwan-CSRF"), csrf);
    assert.equal(request.headers.get("Content-Length"), "2");
    assert.equal(await request.text(), "{}");
  }
  // The simulated outbound service encodes this body from the response header,
  // just like a Worker returning an identity body with automatic encoding.
  return new Response("synthetic workerd response", {
    headers: { "Content-Encoding": "gzip", "Content-Type": "text/plain" },
  });
};
for (const authenticated of [false, true]) {
  const mf = new Miniflare(convertV4MiniflareOptions({
    ...base, outboundService, ...(authenticated ? { access: { aud } } : {}),
  }));
  try {
    const response = await mf.dispatchFetch(origin + "/");
    assert.equal(response.status, authenticated ? 200 : 403);
    assert.equal(await response.text(), authenticated ? "synthetic workerd response" :
      '{"error_code":"access_required"}');
    if (authenticated) {
      const accepted = await mf.dispatchFetch(origin + "/api", { method: "POST", body: "{}",
        headers: { Origin: origin, "Content-Type": "application/json", "Content-Length": "2",
          "X-Diwan-CSRF": csrf, Cookie: "CF_Authorization=synthetic" } });
      assert.equal(accepted.status, 200);
      assert.equal(await accepted.text(), "synthetic workerd response");
    }
  } finally {
    await mf.dispose();
  }
}
assert.equal(calls, 2);
console.log(JSON.stringify({ runtime: "workerd via Miniflare", authenticated_get: 200,
  authenticated_post: 200, unauthenticated: 403, outbound_calls: calls,
  upstream: "synthetic", live_cloudflare_verified: false }));
