# Contributing

Thanks for helping. Bug reports with the agent card, the request and the `trace` that
shows the problem are the most useful contribution there is.

## Setup

You need [uv](https://docs.astral.sh/uv/). `uv.lock` is committed and CI installs with
`--locked`, so run `uv lock` and commit the lock whenever you change `pyproject.toml`.

```bash
git clone https://github.com/monaccode/astromesh-a2a
cd astromesh-a2a
uv sync
```

## Tests

```bash
uv run pytest -q                       # core: no extras, no network
uv run --python 3.10 pytest -q         # the oldest supported Python
```

The core suite talks to in-process fake A2A servers and needs no network. Tests for a
framework adapter skip when its extra is not installed; install one extra at a time:

```bash
uv run --extra langchain pytest -q tests/adapters/test_langchain.py
```

## Lint

```bash
uv run ruff check .
uv run ruff format --check .   # CI gate
uv run ruff format .           # writes the fix
```

## Commits

[Conventional commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `chore:`, `ci:`. Add user-visible changes to `CHANGELOG.md` under `[Unreleased]`.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `__version__` in `astromesh_a2a/__init__.py`,
   run `uv lock`, and move `[Unreleased]` in `CHANGELOG.md` to a dated version section.
2. Tag `vX.Y.Z` and push the tag. `.github/workflows/release.yml` checks that the tag
   matches the version, runs the tests and lint, builds with `uv build`, publishes to
   TestPyPI, installs that build from TestPyPI and smoke-tests it, and only then publishes
   to PyPI. Both uploads use trusted publishing and skip files that already exist.

Trusted publishing needs a one-time setup by a maintainer, on both indexes: a publisher for
the `astromesh-a2a` project with owner `monaccode`, repository `astromesh-a2a` and workflow
`release.yml` (environment `testpypi` on test.pypi.org, `pypi` on pypi.org), and both
environments created in the GitHub repository settings.
