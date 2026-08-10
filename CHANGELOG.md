# Changelog

## [Unreleased]

### Changed

- Activation no longer requires a one-time code. The admin adds a customer by
  name + email in `admin_dashboard.command`; the customer activates in the app
  with that same name + email. The license still binds to the first machine
  that activates — use **Reset Machine** to free it for a new device.

## [7.1.1] - 2026-08-10 -- Server-side license service

### Fixed

- Clean Windows installs no longer fail with `Database Connection Error` on
  first login; activation now uses the public HTTPS license API.
- Admin machine resets now display the replacement one-time activation code.
- Usage counters cannot be reduced through the API.

### Security

- Google service-account credentials moved exclusively to Cloudflare Worker
  secrets and were removed from desktop and legacy application-data folders.
- Added one-time activation codes, machine-bound revocable signed tokens,
  generic public errors, strict input validation, admin bearer authorization,
  security headers, request limits, and Cloudflare rate limiting.

### Added

- Cloudflare Worker tests, type checks, dependency audit, deployment dry-run,
  structured safe error diagnostics, and a license operations runbook.
- Release builds now require and embed only the public license API URL.

## [7.1.0] - 2026-08-09 -- Portable desktop distribution

### Added

- Portable PyInstaller builds for Windows and macOS with Python dependencies,
  FFmpeg/ffprobe, application assets, and the Kokoro model plus all configured
  English voice packs embedded.
- Verified GitHub Release auto-updater with platform-specific assets,
  SHA-256 validation, safe archive extraction, external replacement, and restart.
- Native manual build launchers (`BuildDist.command` and `BuildDist.bat`) and
  GitHub Actions workflows for CI and two-platform releases.
- Windows-first `BuildDist.command` release flow with optional macOS build,
  native GitHub runners, automatic Release publication, and artifact download.
- Local Git repository, release documentation, portable-runtime tests, and a
  single semantic `VERSION` source.

## [7.0.0] - 2026-08-08 -- Rebrand to AmzFlow AI

### Added

- Rebranded product (formerly Ez AmazTube Pro) with a light Orange/Green/White
  design system (`web_app/static/theme.css`), a shared `base.html` layout, and
  one shared JS core (`static/js/core.js`) replacing five duplicated
  CSRF-wrapper/modal/toast implementations.
- Two dedicated creation modules: **URL to Video** (`/create/url`) and
  **Keywords / ASINs to Video** (`/create/keywords`), each with live ASIN
  validation against the Amazon Creators API, sharing one Render Options
  panel and one dashboard with a setup-health checklist.
- Data-driven AI and TTS provider settings: live model/voice catalogs fetched
  per provider with a 24h cache and offline fallback
  (`model_catalog.py`, `tts_catalog.py`), replacing hardcoded `<option>` lists
  (17 Edge voices, 11 Kokoro voices, 5 free-text LLM model fields).
- Async, cached voice preview (`preview_service.py`, `tts_engine.py`) shared
  by the preview route and the render pipeline -- repeat previews are
  instant, and slow providers no longer block a Flask worker.
- Whole-video playback speed control (0.75x-1.5x), applied to picture and
  narration together after final assembly.
- Captions (`captions.srt`) are now actually written per project (the
  generator carried the code path but never called it).
- `elevenlabs_model_id` is now a real, configurable setting.

### Fixed

- An apostrophe in the configured channel name broke every render's
  filtergraph (`LOGO_TEXT` reached `drawtext` unsanitized).
- Changing the configured voice did not invalidate cached narration audio,
  so a re-render could replay the old voice.
- A partially failed multi-ASIN batch could write a script in multi-product
  mode but render it through the single-product timeline.
- `100% Cotton`-style titles rendered as `100%% Cotton` (title text was
  sanitized twice).
- Shorts-mode (9:16) renders could mix a 16:9 slideshow segment into a 9:16
  timeline in one image-insertion branch.
- A failed silence-detection probe made final QC report zero unexpected
  silences instead of failing closed.
- One malformed music-library entry silently disabled background music for
  an entire batch instead of just that track.
- Streaming video/image downloads were never closed and could leave partial
  files on disk on a size-cap abort.
- `ffprobe`/`ffmpeg` calls had no timeout and could hang a render worker
  indefinitely.
- A scrape that returned a page without a recognizable product title (bot
  wall, dead ASIN) proceeded to prompt the LLM with `ORIGINAL TITLE: None`
  instead of skipping the ASIN.

### Changed

- Existing `Ez AmazTube Pro` installs are migrated forward automatically (data
  copied, nothing deleted) to the new `AmzFlow AI` application-data directory.
- TTS provider dispatch (`_tts_provider_once`) now delegates to the shared
  `web_app/tts_engine.py` instead of maintaining a second, drifted copy of
  each provider's HTTP call.

## [6.3.0] - 2026-07-27

### Added

- Fail-closed narration/render validation and final FFmpeg A/V quality report.
- Unique keyword/project folders with complete reusable YouTube packages.
- List and countdown product ordering across narration, chapters, links, and UI.
- Local Kokoro voice, TTS caching, pronunciation normalization, and fallbacks.
- Ten original ambient music beds with category routing and voice ducking.
- Captions, chapters, SEO/title variants, thumbnail variants, and source records.
- Explicit local-save and resumable YouTube-publish workflow with processing state.
- Output-folder, content-evidence, order, music, and intro controls.
- Responsive local Tailwind build and browser smoke tests.

### Fixed

- Videos could succeed after narration or rendered segments silently failed.
- Stream-copy concatenation could retain unstable audio timing and gaps.
- Single-product crossfade filter was constructed but discarded.
- Product order, ranks, affiliate links, and metadata could disagree.
- Reused keyword folders could mix stale assets into a new render.
- YouTube upload could select an arbitrary MP4 instead of the final output.
- Settings exposed credentials to the browser and source files stored secrets.
- State-changing routes lacked CSRF protection and path traversal boundaries.

### Changed

- Secrets/runtime state now live in the per-user application-data directory.
- YouTube scopes are narrower and credentials no longer rotate automatically.
- Cartesia defaults target its current API version/model.
- The app binds locally with debug mode disabled.
