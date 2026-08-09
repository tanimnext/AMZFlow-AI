# GitHub and Release Setup

## First-time repository setup

This folder is already initialized as a local Git repository. Create an empty
public GitHub repository, then run:

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
gh auth login
```

Do not add API keys, OAuth JSON, generated videos, `venv`, `node_modules`, or
build output. They are covered by `.gitignore`, but inspect `git status` before
every commit.

## Create a release

On macOS, double-click `BuildDist.command`, enter the **New Version**, and choose
whether macOS should also be built. Windows is selected by default. The command:

1. checks that GitHub login, `origin`, commits, and push state are ready;
2. starts native GitHub runners (never cross-compiles Windows on a Mac);
3. embeds Python, packages, FFmpeg/ffprobe, models, voices, and app assets;
4. publishes a GitHub Release for the built-in auto-updater; and
5. downloads the ZIP and SHA-256 file into the local `release/` folder.

The source must be committed and pushed before clicking the builder. This makes
the local files and the source used by the Windows runner identical.

The browser alternative is GitHub → Actions → **Release portable desktop
apps** → **Run workflow**. Enter a version such as `7.1.0`, then choose Windows,
macOS, or both. It publishes the selected native artifacts:

- `AmzFlow-AI-X.Y.Z-windows-x64.zip` and `.sha256`
- optionally, `AmzFlow-AI-X.Y.Z-macos-arm64.zip` and `.sha256`

Alternatively, update `VERSION` and `package.json`, commit, and push a semantic
tag:

```bash
git tag v7.1.0
git push origin v7.1.0
```

Every GitHub-built app embeds `owner/repository`; this is how its updater finds
future releases. A local `BuildDist` build can embed a repository by setting
`GITHUB_REPOSITORY=owner/repository` before running it.

The built-in updater uses GitHub's anonymous Releases API, so the update
repository and its release assets must be public. Keep source in a separate
private repository if necessary, and publish the four release assets to a small
public distribution repository.

## Signing recommendation

Before selling/distributing broadly, add a Windows Authenticode certificate and
an Apple Developer ID Application certificate plus notarization. Until those
credentials are configured, Windows SmartScreen and macOS Gatekeeper can warn
customers even when the SHA-256 is correct.
