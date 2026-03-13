# Releasing ha-dominion-energy

## Overview

Releases create a `dominion_energy.zip` asset attached to the GitHub release, which HACS uses to install the integration. The workflow is triggered by creating a GitHub release.

## Steps

1. **Ensure CI is green on `main`** (Hassfest, Type Check, Ruff).

2. **Update `manifest.json`:**
   - Bump `version` to the new version
   - Update `requirements` if the `dompower` dependency changed (ensure the new version is already published to PyPI)

3. **Commit and push:**
   ```bash
   git add custom_components/dominion_energy/manifest.json
   git commit -m "Bump version to X.Y.Z"
   git push
   ```

4. **Wait for CI to pass** on the version bump commit.

5. **Create a GitHub release:**
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes "Release notes here"
   ```
   Or create via the GitHub UI at https://github.com/YeomansIII/ha-dominion-energy/releases/new.

6. **The `Release` workflow runs automatically:**
   - Checks out the repo
   - Updates `manifest.json` version from the git tag (ensures tag and manifest match)
   - Creates `dominion_energy.zip` from `custom_components/dominion_energy/`
   - Attaches the ZIP to the GitHub release

7. **Verify** the release asset is attached at https://github.com/YeomansIII/ha-dominion-energy/releases.

## Versioning

Follow [semver](https://semver.org/):
- **Patch** (1.3.x): Bug fixes, dependency bumps
- **Minor** (1.x.0): New sensors, new features
- **Major** (x.0.0): Breaking changes (config flow changes, removed sensors)

## Release Order

If both `dompower` and `ha-dominion-energy` need releases:

1. Release `dompower` first and wait for PyPI publish to complete
2. Update `manifest.json` with the new `dompower==X.Y.Z` requirement
3. Release `ha-dominion-energy`

This ensures Home Assistant can resolve the `dompower` dependency when users install the update.
