# Spec: Portable Desktop Distribution and Updates

## Objective

Ship AmzFlow AI as a portable Windows and macOS desktop download. A customer
unpacks one ZIP and launches the app without installing Python, FFmpeg, or
Python packages. Application data survives upgrades, and release builds can
check GitHub Releases, verify a SHA-256 checksum, install a newer platform ZIP,
and restart the app.

## Tech Stack

- Python 3.12 and the existing Flask application
- PyInstaller onedir desktop bundles
- FFmpeg and ffprobe copied into each platform bundle
- GitHub Actions and GitHub Releases
- A small, separately frozen updater process for replacing a stopped bundle

## Commands

- macOS local ZIP: `./BuildDist.command 7.1.0`
- Windows local ZIP: `BuildDist.bat 7.1.0`
- Tests: `venv/bin/python3 -m unittest discover -s tests -v`
- Compile: `venv/bin/python3 -m compileall app_files web_app scripts`
- GitHub release: run the **Release portable desktop apps** workflow with a
  semantic version, or push a `vX.Y.Z` tag.

## Project Structure

- `desktop_main.py`: frozen app entry point and render-worker dispatch
- `desktop_updater.py`: independent update installer
- `web_app/runtime_support.py`: frozen/source paths and bundled binaries
- `web_app/update_manager.py`: release lookup, download, checksum, handoff
- `scripts/build_dist.py`: local/CI PyInstaller and ZIP builder
- `.github/workflows/`: test and two-platform release automation
- `dist/`: ignored local release output

## Code Style

Keep platform and version decisions pure where possible, and perform network or
filesystem effects only at the outer edge:

```python
asset = select_release_asset(release, platform_key())
if asset and is_newer(asset.version, current_version()):
    stage_verified_update(asset)
```

## Testing Strategy

- Unit tests cover semantic versions, platform asset selection, frozen paths,
  bundled binary lookup, checksum verification, and archive traversal safety.
- Existing application tests remain the regression gate.
- GitHub builds each OS on its native runner; successful ZIP creation is the
  packaging smoke test.

## Boundaries

- Always: store user settings/OAuth/output outside the install directory;
  verify release SHA-256 before replacement; build each OS on that OS.
- Ask first: code-signing identities, notarization credentials, publishing a
  real GitHub release, or changing customer credentials.
- Never: bundle secrets, copy the developer venv, update from an unverified
  archive, or delete user data during an app upgrade.

## Success Criteria

1. The Windows ZIP starts through `AmzFlow AI.exe`; macOS starts through
   `AmzFlow AI.app`.
2. Python packages, FFmpeg, ffprobe, the Kokoro model/voices, templates, static
   files, fonts, music, and image assets are included.
3. Render workers work from a frozen executable instead of attempting to run a
   `.py` file with the desktop executable.
4. Builds made by GitHub embed their repository identity and can update from
   that repository's latest non-draft release.
5. The updater selects only the current platform asset, validates its matching
   `.sha256` asset, waits for the app to stop, replaces only the install bundle,
   and restarts it.
6. `BuildDist.command` and `BuildDist.bat` create a versioned ZIP on the host OS.
7. Generated output, venvs, node modules, build output, credentials, and caches
   are excluded from Git.

## Open Questions

- Windows Authenticode and Apple Developer ID certificates are not currently
  available. Unsigned builds work but may show SmartScreen/Gatekeeper warnings.
- The first public GitHub repository URL/visibility still needs to be chosen.
  CI-built downloads configure themselves from `GITHUB_REPOSITORY`.
