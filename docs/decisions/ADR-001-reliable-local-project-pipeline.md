# ADR-001: Fail-Closed Rendering and Immutable Local Project Packages

## Status

Accepted

## Date

2026-07-27

## Context

The previous pipeline could discard failed audio/video segments and still
publish a partially valid result. Keyword folders were reused, making stale
files eligible for later runs. Publishing and local generation were coupled,
so the saved artifact was not a dependable source of truth.

## Decision

Every planned narration/render segment is required. Any missing or implausible
segment fails the job. The final encoded media passes stream, duration, and
silence checks before becoming `READY`.

Each successful run creates an immutable, unique package below a keyword folder.
That package, including `project.json` and `qc_report.json`, is the only unit
eligible for explicit YouTube publishing.

## Alternatives Considered

### Best-effort rendering

Faster when one provider request fails, but creates misleading videos with
missing narration. Rejected because a visible failure is safer and cheaper than
publishing damaged content.

### Reusing one folder per keyword

Simple to browse, but stale media and metadata can contaminate later runs.
Rejected in favor of keyword grouping plus unique project IDs.

### Publish directly from temporary render files

Saves one copy step, but makes retry, auditing, and later publishing fragile.
Rejected because the saved package should be independently usable.

## Consequences

- Provider or FFmpeg errors stop generation and are visible in the UI.
- Successful projects use more disk space but remain reproducible and auditable.
- Publishing can be retried without regenerating the video.
- Project deletion remains blocked until YouTube reports successful processing.

