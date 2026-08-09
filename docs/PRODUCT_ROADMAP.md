# Product Roadmap

## Shipped in 6.3

### Reliability and audio

- Fail the job when any required narration or render segment is missing.
- Retry/chunk/cache TTS; normalize common product units and abbreviations.
- Re-encode the final timeline to H.264/AAC with loudness normalization.
- Crossfade video and audio correctly, duck music below voice, and reject long
  unexpected silence with a machine-readable QC report.
- Use local Kokoro as the new-install default, with Edge, ElevenLabs, Cartesia,
  Gemini, and AI33Pro adapters available.

### Product-review quality

- Separate spec-based and hands-on-evidence modes so scripts do not invent use.
- Structure reviews around buyer intent, strengths, limitation, best use,
  who should avoid it, and a concise verdict.
- Keep product rank/order synchronized across voice, footage, chapters,
  affiliate links, metadata, and UI.
- Generate captions, chapters, three title options, and thumbnail variants.
- Support both list order (1 → N) and countdown order (N → 1).

### Save and publish

- Save every successful video in a unique keyword/project folder.
- Include final MP4, thumbnail, combined YouTube text, structured metadata,
  captions, sources, project manifest, and QC report.
- Keep save and publish separate. Upload only the QC-passed final file.
- Validate privacy/scheduling, preserve resumable upload state, and poll YouTube
  processing before allowing local deletion.

### UX and safety

- Add output-folder, order, evidence mode, notes, music, and intro controls.
- Bundle ten original ambient tracks and route by category without downloads.
- Use responsive local CSS with no CDN dependency.
- Move credentials out of source, redact settings, validate paths/images/remote
  media, enforce license/auth and CSRF, and bind the app to localhost.

## Shipped in 6.4

### Source research and editorial control

- Analyze 1-20 HTTPS review or roundup URLs with bounded parallel workers,
  per-host serialization, redirect validation, and crash-resumable SQLite jobs.
- Extract and deduplicate Amazon US ASINs, optionally enrich them through the
  Amazon Creators API, and flag products repeated across source articles.
- Review proposed videos in a dense table before generation. Editors can change
  keywords and video type, include or replace ASINs, reorder products, and
  approve only ready jobs.
- Select Gemini TTS models and all supported voices; tune preset, accent, pace,
  energy, warmth, instructions, and pronunciation with production-matched
  previews.
- Keep only `Thumbnail.jpg`, `{keyword}.mp4`, and `youtube.txt` in each final
  keyword folder.

## Next High-Value Releases

These items require new external credentials, YouTube consent, a licensed data
source, or product decisions and therefore are not silently activated.

### 6.5 — Claim provenance and timeline editing

1. Store claim-level provenance and flag conflicting/missing specs before script
   generation.
2. Add timeline preview/edit controls for captions, overlays, B-roll, and CTA.
3. Add reusable visual templates by niche (tech, beauty, home, automotive).

Acceptance: every factual claim links to source data; the user can preview and
override pronunciation/placement without redoing the full project.

### 6.6 — YouTube optimization loop

1. Add YouTube Analytics read-only consent as an optional, separate connection.
2. Import impressions, CTR, retention, traffic source, and conversion notes.
3. Compare title/thumbnail variants without making unsupported causal claims.
4. Recommend hook length, thumbnail density, and chapter placement from the
   channel's own history.
5. Add publishing calendar, queue, retry history, and channel-level presets.

Acceptance: no analytics permission is requested unless enabled; all advice
shows its evidence window and confidence; publishing remains explicit.

### 6.7 — Production-scale workflow

1. Replace in-memory job/upload state with SQLite and a persistent worker queue.
2. Support pause/resume/cancel, crash recovery, disk-space warnings, and project
   archive/restore.
3. Add macOS Keychain/Windows Credential Manager adapters.
4. Add signed update packages, diagnostics export, and opt-in crash reports.
5. Add deterministic render manifests for exact regeneration.

Acceptance: app restarts do not lose jobs, secrets are OS-keychain protected,
and every release has an automated migration and rollback path.

## Recommended Voice Strategy

- Free/offline default: Kokoro with pronunciation normalization, sentence-sized
  synthesis, cached output, and QC.
- Free best-effort fallback: Edge TTS; internet-dependent and not treated as a
  guaranteed production SLA.
- Premium quality: ElevenLabs, Cartesia, or Gemini through the existing server
  adapters. Provider keys remain private and usage is explicit.
- Do not clone a person's voice without documented consent.
