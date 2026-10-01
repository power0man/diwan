# Private Cloudflare to HF bridge — #257

This is a deployment candidate, with no active route or published endpoint.
It forwards the single owner's authenticated traffic to the existing **private**
`Hussain091/diwan-cloud-limited` Space. It does not make the Space public, change
the running Mac tunnel, add persistent storage, or run a development agent.

## Boundary

Cloudflare Access authenticates the direct Worker invocation. The Worker checks
the runtime's `ctx.access.aud` against one configured audience; a caller's JWT,
email header or cookie never substitutes for that runtime context. No static
assets or service binding router may sit ahead of this Worker: those invocations
do not carry `ctx.access`. The existing single-owner Access policy must be checked
before routing. This bridge is not a multi-user session isolation layer.

The origin and allowed paths are checked before any network call. POST `/api`
requires the original exact public Origin, JSON, a bounded complete body, and the
product's CSRF token. Only then is Origin translated to the **fixed** HF origin.
The HF app retains its exact Host/Origin and secret CSRF comparisons. A valid
token's format is checked here; the app checks its actual value.

The service's `HF_TOKEN` belongs in a Worker secret with read access only to this
Space where the provider supports that scope. Incoming cookies, Authorization,
Access assertions, forwarded hosts and addresses are discarded. Redirects are
not followed. The response streams without buffering; only content type crosses
from its headers. Cookies, redirects and authentication headers do not reach the
browser. Cache use and request logs are disabled because these are private
conversations. An upstream/application response body is still trusted app output;
this bridge does not sanitize arbitrary application HTML or JSON.

## Local verification

Use the repository's locked Python environment and Node 24 or later:

```sh
python -m pytest tests/test_cloudflare_hf_bridge.py
python tools/mutation_check.py --manifest tests/mutations/test_cloudflare_hf_bridge.jsonl
cd deployment/cloudflare-hf
npm ci
node verify_runtime.mjs
npx wrangler types
npx wrangler deploy --dry-run --outdir dist
```

The synthetic handler tests run without npm dependencies, external services, or
credentials and participate in normal pytest/CI. Mutation tests exercise the
actual JS handler. Local synthetic `ctx.access` does **not** prove that a live
Cloudflare deployment authenticated a request. A synthetic workerd smoke test is
also required by the hosted Python 3.14 CI job; it verifies the runtime's Access
context, outbound body and cache options. A live Access smoke test remains required
before switching the domain.

## Deployment and rollback gates

1. Accept the code and current-head CI/review. Keep the existing main protection.
2. Verify the private Space is running, existing account allowance is sufficient,
   and its app origin remains its HF hostname. Do not change Space visibility.
3. Stage this Worker with no production route, no `workers.dev` or preview URLs,
   and no credentials yet. Check its deployment digest against the reviewed code.
4. Configure a dedicated staging hostname and an owner-only Access application.
   Set `PUBLIC_ORIGIN` and its precise `ACCESS_AUD`; install the HF read secret
   through Wrangler secret input, never argv, source, logs, or a browser bundle.
5. Verify authenticated HTML/assets/API, a real CSRF success and failure, denied
   anonymous/forged/cross-site requests, no token in responses, and no cache reuse.
   Test a complete synthetic turn separately: this source-only PR does not claim it.
6. After storage and acceptance gates pass, save the existing DNS/Access/tunnel
   settings and activate the narrow production Worker route with the production
   origin/audience. The blank audience in this checked-in config fails closed.
   Routing and Access policy changes are separate operations; never briefly expose
   the HF credential through an unprotected route.
7. Roll back by removing only the bridge's exact production route. That restores
   the existing tunnel **only while the Mac is running**. Freeze writes before a
   data rollback; routing alone does not reconcile divergent session state.

No secret, Access audience, live route, staging resource, or plan upgrade is
created by installing dependencies, generating types, or a dry run.

Official references checked 2026-10-01:
- [Runtime Access identity and limitations](https://developers.cloudflare.com/workers/configuration/cloudflare-access/)
- [Workers best practices](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/)
- [HF Space visibility](https://huggingface.co/docs/hub/spaces-overview)
- [HF custom domain restrictions](https://huggingface.co/docs/hub/spaces-custom-domain)
