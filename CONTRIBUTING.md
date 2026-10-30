# Contributing to Ritvo

Thank you for your interest. Ritvo is early and pre-alpha, so please open an issue to discuss a change before writing a large pull request.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## What is in scope

Ritvo decides who may approve what when an agent asks to do something outside its bindings, how that decision escalates, and keeps the record. Behaviour and interfaces are defined in [docs/SPEC.md](docs/SPEC.md); a change in behaviour starts with a change to the spec.

Out of scope are the [non-goals in the README](README.md#non-goals): in short, anything a gateway already sells (key issuance, budgets, provider proxying, tool allow lists), credential custody, TLS interception, multi-tenancy and vertical policy content. The mock control plane (`mock_control_plane/`) is a deliberately crude test harness; pull requests that add proxying, spend enforcement or credential custody to it will not be accepted.

## Development setup

You need Python 3.12 (3.13 also works) and git. Ritvo is developed and tested on Linux, macOS and Windows.

```sh
git clone https://github.com/Ritvo-ai/ritvo.git
cd ritvo
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Install the git hooks with [pre-commit](https://pre-commit.com) (installed separately, for example with `pipx install pre-commit`):

```sh
pre-commit install
```

This installs both the pre-commit hooks (ruff, gitleaks, end-of-file fixer) and the `commit-msg` hook that checks commit messages and sign-off.

A map of the code, how the commands flow and where a common change goes is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Running the checks

```sh
ruff check .
ruff format --check .
mypy
pytest
```

CI runs the same checks on Linux, macOS and Windows, plus a licence check on runtime dependencies, a secret scan, a wheel build and install test, CodeQL and the commit hygiene check.

## Platform rules

- Use `pathlib`. Never build paths with string concatenation or assume `/`.
- Spawn subprocesses without a shell, and resolve commands with `shutil.which`.
- Close SQLite connections explicitly in tests; files can stay locked on Windows.
- Tests never touch a real keyring or a real network service.

## Commits

### Sign-off (DCO)

Every commit must carry a `Signed-off-by` line, certifying the [Developer Certificate of Origin](https://developercertificate.org/). Use:

```sh
git commit -s
```

The sign-off must use the same name and email as the commit author. CI fails a pull request in which any commit lacks it.

### Message style

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`, with an optional scope, for example `feat(inspector): read VS Code MCP config`.

- Imperative mood: "add", not "added" or "adds".
- One concern per commit.
- No emoji.
- Commit messages, pull request titles and descriptions must not carry tool or assistant attribution lines, such as "generated with" lines, `Co-authored-by` trailers that name a tool, or session links. The `commit-msg` hook and the CI `commit-hygiene` job check this.

Pull requests are squash-merged, and the pull request title becomes the commit title, so write the title in the same style.

## Data, secrets and fixtures

The repository is public.

- Never commit real credentials, keys or tokens, client configuration, client documents or diagnostic answers, in files, fixtures or history.
- Examples and fixtures use fictional organisations and people.
- A fixture that has to look like a secret (a test token, a test key) goes under a `tests/fixtures/fake/` directory and must be obviously fake. That directory is the only place the secret scan allows such values.
- `.env` files and local databases stay out of git (see `.gitignore`).

## Dependencies

Runtime dependencies must be under Apache-2.0, MIT, BSD, ISC, PSF or a similar permissive licence. Open an issue before proposing a dependency under any other licence. CI checks the licences of runtime dependencies.

## Pull requests

Fill in the pull request template: what changed, which part of the spec it relates to, and how it was tested. Keep pull requests small and focused.
