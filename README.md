# AmzFlow AI

Local Amazon US product-review video workflow: turn a review URL or a
keyword+ASIN list into a narrated, branded video, review it, and explicitly
publish to YouTube later.

## Quick Start — Portable Desktop

Download the ZIP for your operating system from GitHub Releases, unzip it, and
launch **AmzFlow AI.exe** on Windows or **AmzFlow AI.app** on macOS. Python,
FFmpeg, ffprobe, fonts, music, and application assets are included; customers
do not need to install them separately. Settings, OAuth tokens, and generated
videos are stored outside the application bundle so an update does not remove
them.

Release builds check their GitHub repository at startup. A newer platform ZIP
is downloaded only with its matching SHA-256 file, verified, installed by the
separate updater, and restarted. Set `AMZFLOW_DISABLE_AUTO_UPDATE=1` only when
troubleshooting an update.

## Developer Quick Start

Requirements: macOS, Python 3.12, FFmpeg/ffprobe, and the dependencies listed
in `web_app/requirements_web.txt`.

```bash
./run_app.command
```

The launcher first migrates credentials and runtime state to:

```text
~/Library/Application Support/AmzFlow AI/
```

(existing installs from `Ez AmazTube Pro` are copied forward automatically,
once, on first launch -- nothing is deleted from the old location). It then
opens the local Flask application on `http://127.0.0.1:7503`.

## Commands

| Command | Purpose |
|---|---|
| `./run_app.command` | Migrate private data and start the app |
| `venv/bin/python3 -m unittest discover -s tests -v` | Run all tests |
| `venv/bin/python3 -m compileall app_files web_app scripts` | Compile check |
| `venv/bin/python3 -m pip check` | Verify installed dependencies |
| `npm run build:css` | Rebuild the local Tailwind UI CSS (run after any template edit) |
| `./admin_dashboard.command` | Local license/user admin dashboard (port 7510) |
| `./BuildDist.command 7.1.0` | Build a macOS portable release ZIP |
| `BuildDist.bat 7.1.0` | Build a Windows portable release ZIP |

Windows must be built on Windows and macOS on macOS. To create both downloads,
push the repository to GitHub and run **Release portable desktop apps** from
the Actions tab with an `X.Y.Z` version. The workflow builds both native apps,
creates checksum files, and publishes a GitHub Release.

## Two Creation Modules

- **URL to Video** (`/create/url`) -- paste 1-20 authority-site review or
  roundup URLs. The app fetches each one (SSRF-hardened, redirect-revalidated,
  size-capped), extracts ASINs, validates them against the Amazon Creators
  API when configured, and presents each proposed video in a human-review
  table. Edit the keyword, switch single/roundup mode, include/exclude
  products, replace ASINs, reorder, and approve only the rows that should
  enter generation.
- **Keywords / ASINs to Video** (`/create/keywords`) -- already know the
  products? Paste `keyword, ASIN1, ASIN2, ...` lines directly and check them
  against Amazon before generating.

Both modules share one Render Options panel (voice, format, product order,
background music, intro clip, whole-video speed, output folder) and hand off
to the same generator.

## Final Output

Every successful generation creates one keyword folder:

```text
{selected-output-root}/{keyword}/
```

After generation finishes, intermediate files and subfolders are removed. The
folder retains only `Thumbnail.jpg`, `{keyword}.mp4`, `youtube.txt`, and
`captions.srt`. YouTube publishing is a separate, explicit action from the
Studio page.

## Key Behaviour

- AI provider and TTS provider/voice/model lists are data-driven: model and
  voice catalogs are fetched live from each provider (with a 24h cache and a
  built-in fallback list when offline), not hardcoded in the UI.
- Voice previews are cached and run as an async job -- repeat previews of the
  same settings are instant, and a slow provider (e.g. AI33 Pro) never blocks
  the page.
- Kokoro is the default free, local voice; Edge and configured cloud
  providers (ElevenLabs, Cartesia, AI33 Pro, Gemini) remain optional.
- Gemini TTS offers model selection, all supported preset voices, performance
  styles, accent, pace, energy, warmth, director instructions, pronunciation
  overrides, and matching preview/final-render behavior.
- Whole-video playback speed (0.75x-1.5x) is adjustable per render, applied
  to picture and narration together after final assembly.
- Required narration or render failures stop the job instead of silently
  creating a video with missing sound.
- Final FFmpeg QC verifies streams, duration, and unexpected long silence.
- Product order can be list order (1 → N) or countdown (N → 1).
- Ten bundled original ambient beds are selected by product category and
  automatically ducked below narration.
- Settings and credentials are stored outside the source folder, and browser
  settings responses redact secret fields.
- Upload requires a QC-passed `video.mp4`; YouTube processing status is tracked
  before project deletion is allowed.

## External Setup

Rotate any API/OAuth/service-account credentials that previously existed in
this source folder. Re-authorize the intended YouTube account after the scope
change. A live upload, OAuth consent, paid provider activation, and key rotation
are deliberately not performed automatically.

See [IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md) and
[ADR-001](docs/decisions/ADR-001-reliable-local-project-pipeline.md).
