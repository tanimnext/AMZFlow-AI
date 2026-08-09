# GitHub and Release Setup

## First-time repository setup

This folder is already initialized as a local Git repository. Create an empty
public GitHub repository, then run:

```bash
git add .
git commit -m "feat: add portable desktop distribution"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

Do not add API keys, OAuth JSON, generated videos, `venv`, `node_modules`, or
build output. They are covered by `.gitignore`, but inspect `git status` before
every commit.

## Create a release

The easiest route is GitHub → Actions → **Release portable desktop apps** →
**Run workflow**, then enter a version such as `7.1.0`. It builds on native
Windows and macOS runners and publishes:

- `AmzFlow-AI-X.Y.Z-windows-x64.zip` and `.sha256`
- `AmzFlow-AI-X.Y.Z-macos-arm64.zip` and `.sha256`

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
