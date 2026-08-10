# License API Operations

## Production

- Worker: `amzflow-license-api`
- URL: `https://amzflow-license-api.tanimnext2.workers.dev`
- Sheet: `Sheet1!A:J`
- Desktop release variable: GitHub repository variable `LICENSE_API_URL`

The Worker is the only component that may hold the Google service-account JSON.
Never put that credential, `LICENSE_SIGNING_SECRET`, or `ADMIN_API_TOKEN` in a
release ZIP, repository variable, log, issue, or commit.

## Deploy

Authenticate the owner workstation, upload secrets through stdin, then deploy:

```bash
npx --prefix license-worker wrangler login
npx --prefix license-worker wrangler secret put GOOGLE_SERVICE_ACCOUNT_JSON --config license-worker/wrangler.jsonc
npx --prefix license-worker wrangler secret put GOOGLE_SHEET_ID --config license-worker/wrangler.jsonc
npx --prefix license-worker wrangler secret put LICENSE_SIGNING_SECRET --config license-worker/wrangler.jsonc
npx --prefix license-worker wrangler secret put ADMIN_API_TOKEN --config license-worker/wrangler.jsonc
npx --prefix license-worker wrangler deploy --config license-worker/wrangler.jsonc
```

Use a password manager or a protected file as stdin. Do not paste secret values
into command arguments because shell history and process listings may retain
them.

## Verify

```bash
curl --fail --silent --show-error \
  https://amzflow-license-api.tanimnext2.workers.dev/v1/health
npm --prefix license-worker test
npm --prefix license-worker run typecheck
```

The owner-only admin dashboard reads its bearer token from
`AMZFLOW_LICENSE_ADMIN_TOKEN` or the private application-data file
`license_admin_token.txt`. That file is never bundled.

## Rotate and Revoke

1. Upload a new Worker secret with `wrangler secret put`.
2. Deploy and verify `/v1/health` plus an authenticated admin list request.
3. Revoke the old credential at its provider.
4. For a customer token, use **Reset Machine** or generate a new activation
   code; both increment `TokenVersion` and revoke previously issued tokens.

## Rollback

Use Cloudflare Worker version rollback for API regressions. Keep the previous
desktop GitHub release available and do not restore direct Google access in the
client. A rollback is complete only after public health and authenticated Sheet
read both succeed.
