# Contributing

See the [Development section of the README](README.md#development) for the local
setup (uv, Ruff, mypy, pytest).

## Pre-commit hooks

After `uv sync`, enable the git hooks once:

```bash
uv run pre-commit install
```

They run Ruff (lint + format) and mypy on each commit — the same checks as CI,
so failures surface before you push. To run them across the whole repo on
demand:

```bash
uv run pre-commit run --all-files
```

## Commit and PR conventions

Releases are automated with
[release-please](https://github.com/googleapis/release-please), which reads the
commit history to build the changelog and pick the next version. That means
commit messages need to follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore`. A `!` after the type (e.g. `feat!:`) or a `BREAKING CHANGE:` footer
marks a breaking change.

While we're pre-1.0, breaking changes bump the minor and everything else bumps
the patch:

| Commit | Bump | Example |
|---|---|---|
| `fix:` / `feat:` | patch | 0.2.0 → 0.2.1 |
| `feat!:` / `BREAKING CHANGE:` | minor | 0.2.0 → 0.3.0 |

We squash-merge PRs, so **the PR title becomes the commit on `main`** — which is
what release-please parses. A CI check validates that every PR title is a valid
Conventional Commit; fix the title if it fails.

## Releasing

You don't tag or edit the changelog by hand. release-please keeps an open
"release" PR that accumulates every change merged to `main` and updates the
`CHANGELOG.md` and version. When you're ready to cut a release, merge that PR —
it creates the tag and GitHub release.
