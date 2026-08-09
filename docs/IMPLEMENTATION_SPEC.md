# Spec: Ez AmazTube Pro Reliability and Product Upgrade

## Objective

Turn the current local Flask/FFmpeg tool into a safe, reliable product-review
video workflow. A successful run must never silently omit narration, must keep
product order consistent, must produce a reusable local project package, and
must publish to the explicitly selected YouTube channel only after QC passes.

## Tech Stack

- Python 3 / Flask
- FFmpeg and ffprobe
- Local Kokoro TTS with Edge TTS as a best-effort fallback
- Existing Google YouTube Data API integration
- Server-rendered HTML, CSS, and JavaScript

## Commands

- Compile: `venv/bin/python3 -m compileall app_files web_app`
- Unit/integration tests: `venv/bin/python3 -m unittest discover -s tests -v`
- Dependency check: `venv/bin/python3 -m pip check`
- Run: `./run_app.command`

## Project Structure

- `app_files/`: generation, TTS, metadata, and thumbnail pipeline
- `web_app/`: Flask routes and user interface
- `tests/`: deterministic unit and Flask integration tests
- `docs/`: product specification and operational documentation
- `files_created/`: generated project packages only

## Code Style

Validate once at external boundaries and pass safe, typed values inward:

```python
project_dir = resolve_project_dir(request.form.get("project_id", ""))
order = parse_product_order(request.form.get("product_order", "countdown"))
```

Helpers return explicit results or raise a descriptive exception. Rendering
must fail closed: a missing required audio or video segment aborts the run.

## Testing Strategy

- Unit tests for path containment, order selection, manifests, exports, audio
  validation, and metadata formatting.
- Flask integration tests for protected routes and validation failures.
- FFmpeg smoke tests for stream presence, duration, silence, and loudness when
  local fixtures are available.
- Browser smoke test for the generation, save, and publish controls.

## Boundaries

- Always: validate paths/input, use unique project IDs, perform final A/V QC,
  encode untrusted UI text, save atomically, and keep publish state explicit.
- Ask first: paid provider activation, OAuth consent, live upload, deleting a
  generated project, or changing the user's existing API credentials.
- Never: expose secrets to the browser/logs, upload a fallback segment, silently
  drop failed audio/video, claim hands-on testing without evidence, or delete
  local files before YouTube reports successful processing.

## Required Product Behaviour

1. One generation job runs at a time in this desktop process.
2. Every planned narration segment exists, has plausible duration, and reaches
   the final timeline; otherwise generation ends as `QC_FAILED`.
3. The same canonical ordered-product list controls narration, ranks, chapters,
   links, and metadata. Both `list` (1 to N) and `countdown` (N to 1) work.
4. Every project has a unique folder containing at least:
   `video.mp4`, `thumbnail.jpg`, and `youtube.txt`; it also stores
   `project.json`, `metadata.json`, `captions.srt`, `sources.json`, and
   `qc_report.json` when available.
5. Local Save and YouTube Publish are separate actions. Upload is resumable,
   scheduling validates future time, and processing status is tracked.
6. Secrets remain server-side and sensitive files are not distributed as
   templates or exposed by settings endpoints.
7. Review content labels itself as spec-based unless the user provides
   hands-on notes/evidence. Product claims are traceable to source data.
8. Kokoro is the default free/local voice. Edge and cloud voices are optional
   adapters with retry, caching, pronunciation normalization, and provider
   limits.
9. Background music comes from a licensed local library, is routed
   deterministically by category, and is ducked under speech.
10. UI provides output folder, product order, content mode, voice, music,
    Save/Publish choices, validation feedback, and project status.

## Success Criteria

- Automated tests pass and Python files compile.
- Traversal and absolute-path payloads cannot escape approved roots.
- No required segment can be filtered out while a run still succeeds.
- Final QC confirms video+audio streams, expected duration, and no unexpected
  long silence; QC failure blocks publishing.
- Duplicate keywords create distinct project IDs and never reuse stale assets.
- Saved packages contain the required files and synchronized metadata/order.
- Settings responses redact credentials; protected mutations require an active
  license session and same-origin CSRF token.

## External Setup

The user must rotate credentials previously stored in this folder, complete
OAuth for the intended YouTube channel, and provide any optional paid provider
keys. Those irreversible external actions are not performed automatically.
