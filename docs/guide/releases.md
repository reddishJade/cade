# Versions and releases

A release package comes from a fixed Git commit. Version tags use `vMAJOR.MINOR.PATCH`, such as `v0.3.0`. A tag must belong to `main` history and match the version in `pyproject.toml`. Keep published tags and artifacts unchanged. Deliver fixes in a new version.

## Version numbers

Versioning follows [Semantic Versioning 2.0.0](https://semver.org/). During `0.x` development, public interfaces are not guaranteed to be stable. Increment the minor version for breaking changes and the patch version for compatible fixes.

From `1.0`, increment the major version for breaking changes, the minor version for compatible features, and the patch version for compatible fixes. Internal refactoring alone does not require a minor version increment. The current release workflow accepts stable versions only, not prerelease tags.

To inspect the source version:

```sh
uv version --short
```

To inspect the version installed in the current environment:

```sh
uv pip show cade-agent
```

## Repository settings

Before the first release, configure these GitHub rules:

- Prohibit force pushes and deletion for `main` and `dev`. Allow merge commits on both branches.
- Require CI checks only for `main`. Do not require PRs for either branch. `dev` accepts direct development and experimental branch merges without a required status check.
- Promote only the current remote `dev` commit to `main`. Install the [pre-push hook](../development.md#environment-setup) to enforce this rule locally; GitHub branch protection does not enforce the source branch.
- Restrict creation of `v*` tags and prohibit updates or deletion of existing tags.
- Create a `release` environment that permits deployment only from `v*` tags and requires approval. A sole maintainer can allow self-review.

Use check names from actual CI runs when configuring rulesets. The release workflow declares the `release` environment, but you must configure approval rules in GitHub. Declaring an environment does not enable approval automatically. Complete this setup before the first run.

## Prepare a version

Complete integration through the [development workflow](../development.md#daily-product-development). When a version increment is needed, run on `dev`:

```sh
uv version --bump patch
```

Choose `patch`, `minor`, or `major` according to the change. The command updates both the version and lockfile. Review `pyproject.toml` and `uv.lock`. Commit the version change, then promote the verified version to `main`.

When real-provider checks are needed, follow the [validation guidelines](../testing.md). Review results and costs before release.

## Tag a version

With a clean workspace, synchronize `main`:

```sh
git switch main
git pull --ff-only origin main
```

Read the version:

```sh
VERSION="$(uv version --short)"
```

Create the tag:

```sh
git tag -a "v$VERSION" -m "Release v$VERSION"
```

Push only that tag:

```sh
git push origin "v$VERSION"
```

The tag triggers the `Release` workflow. After checks and approval pass, it creates a GitHub Release. If checks fail, fix the cause. If source changes are needed, use a new version rather than moving an existing tag.

## Release artifacts

Inspect the tag's `Release` run in GitHub Actions. The workflow checks tag format, package version, and membership in `main`, then runs product CI against the fixed tagged commit. CI builds a wheel, source archive, and `SHA256SUMS`. Temporary artifacts are retained for seven days. CI does not call model APIs.

After checks pass, approve publication under the `release` environment rules. The publication step verifies that the tag still points to the same commit and checks artifact hashes before creating the GitHub Release. It uses the artifacts built by that CI run without rebuilding. GitHub generates release notes; the maintainer must review them.

The current workflow publishes to GitHub Releases, not PyPI. If publication fails, check whether a Release already exists on GitHub before retrying. Preserve published tags and artifacts.
