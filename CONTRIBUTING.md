# Contributing

Model Evolution accepts focused issues and pull requests against `main`.

## Development setup

```bash
git clone git@github.com:ilikedata/model-evolution.git
cd model-evolution
python -m venv .venv
. .venv/bin/activate
make sync
make check
```

Add or update tests for behavior changes. Keep project-specific training code in
the consuming project's adapter rather than adding model-framework dependencies
to the core package.

## Pull requests

- Keep each commit focused and use descriptive commit messages.
- Run `make check` before opening a pull request.
- Update public documentation and `CHANGELOG.md` with observable changes.
- Preserve compatibility with schema-v2 records or include an explicit migration.

## Releases

Releases are built from annotated `vMAJOR.MINOR.PATCH` tags. The version in
`pyproject.toml` and the changelog must match the tag. GitHub Actions builds the
artifacts and publishes them through PyPI Trusted Publishing after approval in
the protected `pypi` environment.
