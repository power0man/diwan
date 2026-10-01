// A single-owner, fixed-destination bridge. Access is authenticated by the
// Workers runtime, never by a caller-supplied identity header or decoded JWT.
const UPSTREAM = "https://hussain091-diwan-cloud-limited.hf.space";
const MAX_BODY = 512 * 1024;
const GET_PATHS = new Set(["/", "/app.js", "/style.css"]);

function refused(code, status = 403) {
  return Response.json({ error_code: code }, {
    status,
    headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff" },
  });
}

async function boundedBody(request, expected) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("body_missing");
  const bytes = new Uint8Array(expected);
  let length = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > expected) throw new Error("body_limit");
      bytes.set(value, length - value.byteLength);
    }
    if (length !== expected) throw new Error("body_incomplete");
    return bytes;
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export default {
  async fetch(request, env, ctx) {
    try {
      const origin = new URL(env.PUBLIC_ORIGIN);
      if (origin.protocol !== "https:" || origin.origin !== env.PUBLIC_ORIGIN ||
          origin.username || origin.password ||
          !/^[a-f0-9]{64}$/.test(env.ACCESS_AUD ?? "") ||
          !/^hf_[A-Za-z0-9]+$/.test(env.HF_TOKEN ?? "")) {
        return refused("bridge_unconfigured", 503);
      }
      if (!ctx.access || ctx.access.aud !== env.ACCESS_AUD) {
        return refused("access_required");
      }
      const url = new URL(request.url);
      if (url.origin !== env.PUBLIC_ORIGIN || url.search || url.hash) {
        return refused("request_target_refused");
      }
      const post = request.method === "POST" && url.pathname === "/api";
      const get = request.method === "GET" && GET_PATHS.has(url.pathname);
      if (!get && !post) return refused("route_refused");
      const site = request.headers.get("Sec-Fetch-Site");
      const navigation = get && url.pathname === "/" &&
        request.headers.get("Sec-Fetch-Mode") === "navigate" &&
        request.headers.get("Sec-Fetch-Dest") === "document";
      if (!navigation && site !== null && site !== "none" && site !== "same-origin") {
        return refused("cross_site_refused");
      }
      // An allowlist drops Authorization, cookies, Access assertions, forwarding
      // headers, and caller-selected routing. The Space stays private.
      const headers = new Headers({ "Authorization": `Bearer ${env.HF_TOKEN}` });
      for (const name of ["Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest"]) {
        if (request.headers.has(name)) headers.set(name, request.headers.get(name));
      }
      let body;
      if (post) {
        // Validate the *original* browser origin before translating it to HF.
        // The product then performs its unchanged, exact CSRF comparison.
        if (request.headers.get("Origin") !== env.PUBLIC_ORIGIN ||
            !/^[a-f0-9]{64}$/.test(request.headers.get("X-Diwan-CSRF") ?? "")) {
          return refused("csrf_refused");
        }
        const type = request.headers.get("Content-Type");
        const length = request.headers.get("Content-Length") ?? "";
        if (!["application/json", "application/json; charset=utf-8"].includes(type) ||
            !/^[0-9]{1,7}$/.test(length) || Number(length) < 1 || Number(length) > MAX_BODY ||
            request.headers.has("Content-Encoding") || request.headers.has("Transfer-Encoding")) {
          return refused("body_refused");
        }
        body = await boundedBody(request, Number(length));
        headers.set("Origin", UPSTREAM);
        headers.set("Content-Type", type);
        headers.set("Content-Length", String(body.byteLength));
        headers.set("X-Diwan-CSRF", request.headers.get("X-Diwan-CSRF"));
      }
      const target = new URL(url.pathname, UPSTREAM);
      const upstream = await fetch(target, {
        method: request.method, headers, body, redirect: "manual",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(330_000)]),
        cache: "no-store",
        cf: { cacheTtl: 0, cacheEverything: false },
      });
      // Redirects never carry the service token to another destination.
      if (upstream.status >= 300 && upstream.status < 400) {
        await upstream.body?.cancel();
        return refused("upstream_redirect_refused", 502);
      }
      const responseHeaders = new Headers({
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'none'; img-src blob:; media-src blob:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      });
      if (upstream.headers.has("Content-Type")) {
        responseHeaders.set("Content-Type", upstream.headers.get("Content-Type"));
      }
      // No Set-Cookie, Location, Access identity, or upstream auth headers escape.
      // Responses are streamed; no unbounded body buffering or cache writes.
      return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
    } catch {
      // Never expose upstream URLs, credentials, exception text, or stack traces.
      return refused("bridge_unavailable", 502);
    }
  },
};
