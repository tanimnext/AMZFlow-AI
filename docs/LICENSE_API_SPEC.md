# Spec: Server-Side License API

## Objective

Replace direct Google Sheets access from the desktop application with a
Cloudflare Worker API. Google service-account credentials must exist only as a
server-side secret and must never be committed, logged, returned by an API, or
embedded in Windows/macOS distributions.

The existing Google Sheet remains the license database. The admin registers a
customer by name and email only (`admin_dashboard.command`, owner-only,
localhost). The customer then activates with that same registered email, name,
and the local machine ID — no activation code changes hands. The API binds the
license to the first machine that activates and returns a revocable signed
token for later verification and quota updates. Resetting a machine (admin
dashboard) frees the email for one more activation on a new device.

## Approved Decisions

1. Cloudflare Workers is the hosting platform.
2. Existing Google Sheet `Sheet1` remains the source of truth.
3. Email + name registered by the admin is sufficient to activate; no
   activation code is issued or required (decision reversed 2026-08-10 — see
   Deployment Status).
4. The local admin dashboard continues to be owner-only, but calls protected
   API endpoints instead of reading Google credentials.

## API Contract

All responses use JSON and HTTPS. Errors never include Google or Worker
internals.

```text
GET  /v1/health
POST /v1/activations
POST /v1/licenses/verify
POST /v1/usage

GET    /v1/admin/users?page=1&pageSize=50
POST   /v1/admin/users
PATCH  /v1/admin/users/:email
DELETE /v1/admin/users/:email
POST   /v1/admin/users/:email/reset-machine
POST   /v1/admin/users/:email/reset-usage
```

Activation request:

```json
{
  "email": "customer@example.com",
  "name": "Customer Name",
  "machineId": "validated-machine-id"
}
```

Successful activation/verification response:

```json
{
  "data": {
    "activationToken": "returned only by /v1/activations",
    "license": {
      "email": "customer@example.com",
      "name": "Customer Name",
      "used": 0,
      "quota": "Unlimited",
      "expiryDate": "Lifetime",
      "expiryTime": "00:00"
    }
  }
}
```

Every error follows one stable shape:

```json
{
  "error": {
    "code": "INVALID_ACTIVATION",
    "message": "Activation details are invalid."
  }
}
```

Public activation errors are deliberately generic to prevent email/license
enumeration. Admin endpoints require `Authorization: Bearer <admin token>`.
License verification and usage endpoints require the signed activation token.

## Google Sheet Schema

Existing columns A-H remain unchanged:

```text
A Name | B Email | C MachineID | D LastLogin | E Used | F Quota
G ExpiryDate | H ExpiryTime
```

Add:

```text
I ActivationCodeHash | J TokenVersion
```

Column I is unused since the 2026-08-10 email-only activation change (kept
empty; the schema is unchanged to avoid a Sheet migration). `TokenVersion`
allows reset/revocation without keeping issued tokens in the Sheet.

## Server Secrets

Configured with `wrangler secret put`; values never appear in `wrangler.toml`:

```text
GOOGLE_SERVICE_ACCOUNT_JSON
GOOGLE_SHEET_ID
LICENSE_SIGNING_SECRET
ADMIN_API_TOKEN
```

Non-secret Worker configuration contains only allowed origins, API version,
and operational timeouts. The desktop bundle contains only the public HTTPS
API base URL.

## Threat Boundaries

- Untrusted: desktop requests, emails, names, machine IDs, activation codes,
  bearer tokens, Google API responses, and request metadata.
- Protected assets: Google private key, admin token, signing secret, customer
  license data, quota, and machine binding.
- Controls: strict field lengths/formats, constant-time comparisons where
  practical, hashed activation codes, signed expiring tokens, generic public
  errors, admin authorization, no permissive CORS, request-size cap, HTTPS,
  timeouts, and rate limiting at the Worker/Cloudflare boundary.

## Project Structure

```text
license-worker/            Cloudflare Worker (TypeScript, no runtime deps)
license-worker/src/        API, validation, Google auth/storage adapter
license-worker/test/       Worker unit/contract tests
web_app/license_store.py   Desktop/admin HTTPS API client
web_app/secure_paths.py    Private activation-token location
docs/                      Deployment, migration, and rollback instructions
```

## Commands

```bash
npm --prefix license-worker test
npm --prefix license-worker run typecheck
npx wrangler deploy --config license-worker/wrangler.jsonc
venv/bin/python3 -m unittest discover -s tests -v
./BuildDist.command 7.1.1 windows
```

## Testing Strategy

- Worker unit tests: validation, generic errors, token signing/verification,
  expiry, token-version revocation, admin authorization, and Google response
  validation with a fake fetch implementation.
- Python unit tests: API response parsing, timeouts, missing API URL, token
  persistence permissions, and no fallback to local Google credentials.
- Contract tests: the desktop client and Worker fixtures share the same field
  and error shapes.
- Release verification: CI, native Windows build, embedded public API URL,
  checksum, archive inspection, and a staging activation before publication.

## Boundaries

- Always: validate inputs at API boundaries; use generic public errors; keep
  secrets in Cloudflare; verify external response shapes; preserve rollback.
- Ask first: change authentication UX, Sheet schema, or hosting provider;
  migrate existing users; publish a production release.
- Never: bundle/commit Google credentials or admin/signing secrets; log tokens
  or activation codes; let the client call Google Sheets directly.

## Rollout and Rollback

1. Deploy Worker with secrets and `/v1/health` only.
2. Add/migrate Sheet columns I-J and create a test license/code.
3. Verify staging activation, verification, revocation, and usage update.
4. Deploy desktop client integration and local admin integration.
5. Publish Windows `v7.1.1`; monitor Worker errors and activation failures.
6. After successful rollout, remove local service-account files from developer
   machines and rotate the old Google service-account key.

Rollback: keep `v7.1.0` available, revoke `v7.1.1` as latest if activation has
production issues, and redeploy the previous Worker version. Never restore
direct client access to Google credentials.

## Success Criteria

1. A clean Windows installation activates without `config.json`.
2. No distributed or tracked file contains Google credentials.
3. A registered email/name activates on the first machine that requests it;
   unknown emails and already-bound machines return the same generic error.
4. Token verification reflects expiry/quota changes and supports revocation.
5. Admin CRUD/reset actions require the admin token.
6. Worker and Python tests, CI, Windows build, checksum, and staging activation
   all pass before a release is published.

The one-time activation-code requirement was approved on 2026-08-09, then
reversed on 2026-08-10: the owner found generating and relaying a code for
every customer too much overhead for a small-scale license base, and asked
for the original "just add the email" simplicity back. Email + name
registered by the admin, plus per-license machine binding (one active device,
free-able via **Reset Machine**), was kept as the minimum viable protection
against a leaked email being reused on someone else's machine.

## Deployment Status

Production was deployed on 2026-08-10 at
`https://amzflow-license-api.tanimnext2.workers.dev`. Google Sheet columns I-J
were migrated, the existing license was reactivated through the API, and local
Google service-account files were removed after end-to-end verification. The
activation-code requirement was removed the same day (see Approved Decisions).
