# ADR-002: Keep Credentials Outside the Source Tree

## Status

Accepted

## Date

2026-07-27

## Context

Provider keys, OAuth tokens, service-account credentials, activation state, and
runtime upload records existed beside distributable application code. Settings
were also returned to the browser without recursive redaction.

## Decision

All credentials and mutable account state are stored in a permission-restricted
per-user application-data directory. Startup performs an idempotent migration.
The source settings file contains non-secret defaults only. Browser responses
remove secret-named fields recursively, all state-changing requests require
CSRF tokens, and project paths must remain inside the selected library root.

## Alternatives Considered

### Environment variables only

Strong for servers, but awkward for a local desktop workflow with several
provider configurations. Still supported for the Flask session secret.

### Encrypt credentials with an application-owned key

Moves rather than solves the key-storage problem. A future native keychain
adapter is appropriate; embedding a decryption key is not.

## Consequences

- The source folder is safer to copy, archive, or version-control.
- Users must rotate any credential that was previously exposed.
- Backups must include the application-data folder separately.
- A future release can replace file storage with macOS Keychain without
  changing the browser-facing settings contract.

