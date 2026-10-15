# Development guide

`reddishJade` maintains this repository alone. Create and merge your own PRs. Product PRs trigger CI and do not require another person's review.

| Repository | Content | Branches |
| --- | --- | --- |
| `cade` (public) | Product code, regression tests, and user documentation | Integrate on `dev`; deliver on `main` |
| `cade-internal` (private) | Capability evaluations, A/B experiments, and real-provider acceptance tools and tasks | Keep working tools on `main` |

## Environment setup

The existing checkouts are `~/workspace/cade` and `~/workspace/cade-internal`. To recreate the product environment or update the push hook:

```sh
cd ~/workspace/cade
uv sync --locked --extra dev
install -m 755 .githooks/pre-push "$(git rev-parse --git-path hooks/pre-push)"
```

## Daily product development

Create a short-lived branch from the latest `dev` for one task:

```sh
cd ~/workspace/cade
git switch dev
git pull --ff-only origin dev
git switch -c fix/short-description
```

After editing, run the checks:

```sh
uv run --locked ruff check src/cade/
uv run --locked ruff format --check src/cade/
uv run --locked pyright src/cade/
uv run --locked pytest -q src/cade/tests
uv run --locked cade --help
```

Commit after checks pass. Use `git add -p` for tracked files and `git add -- PATH` for new files:

```sh
git add -p
git diff --cached
git commit -m "fix: describe the change"
git push -u origin HEAD
gh pr create --base dev --fill
gh pr checks --watch
```

After CI passes, merge the PR and delete the short-lived branch:

```sh
gh pr merge --squash --delete-branch
```

## Internal evaluation and acceptance

Use a short-lived branch for internal tools too:

```sh
cd ~/workspace/cade-internal
git switch main
git pull --ff-only origin main
git switch -c chore/short-description
```

Run checks from the internal `README.md`, then commit and push. Create the PR with `gh pr create --base main --fill` and squash-merge it yourself. The internal repository has no product CI and does not run models automatically. Fix the Cade commit, task, model, and budget before each experiment.

## Promote to main and release

When a version increment is needed, run `uv version --bump patch` on the working branch. Merge the version and lockfile into `dev` through a PR. After `dev` CI passes, promote the same commit to `main`:

```sh
cd ~/workspace/cade
git switch dev
git pull --ff-only origin dev
git fetch origin
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
```

Create a tag only for a release. Use the current version if it has not been published; otherwise, increment it first:

```sh
VERSION="$(uv version --short)"
git tag -a "v$VERSION" -m "Release v$VERSION"
git push origin "v$VERSION"
```

The tag triggers checks and builds. After they pass, approve publication in GitHub's `release` environment. Do not move published tags; release fixes under new versions. See [Versions and releases](guide/releases.md) for the full policy.

Do not force-push `main` or `dev`. Do not use `git push --all`. Keep internal tools out of the public repository. Do not commit credentials or raw run records.
