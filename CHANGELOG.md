# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project governance: licence, notice, security policy, contributing guide, code of conduct, governance, support and trademark pages.
- Product requirements (`docs/PRD.md`), technical specification (`docs/SPEC.md`) and the first decision record, on why Ritvo sits below gateways.
- Python package skeleton with the `ritvo` CLI entry point (`ritvo --version`).
- Specification of the authority gap review (`docs/SPEC.md` section 8.1): 17 questions in five areas, the answers file, scoring and the one-page result.
- `ritvo review <answers.yaml>`: the authority gap review. Scores 17 answers in five areas and writes a self-contained one-page `review.html`. Example answers in `examples/review/answers.yaml`.
- Specification of the intake templates (`docs/SPEC.md` section 8.2): `agents.csv`, `tools.csv` and `people.csv`, their columns and validation.
- `ritvo diagnose --templates <dir>`: writes the blank intake templates and never overwrites a file. A reader validates filled-in templates for the diagnostic. Examples in `examples/intake/`.
- Specification of the inventory format (`docs/SPEC.md` section 4.8) and of registry exports from AWS Bedrock AgentCore and Google Vertex AI Agent Engine (section 7.3).
- Runtime dependencies Jinja2, Pydantic and PyYAML.
- Continuous integration on Linux, macOS and Windows: lint, type check, tests, licence check, secret scan, wheel build and install test, CodeQL, and commit hygiene.
- Release workflow: on a `v*` tag, test, build, check that the tag matches the package version, attest build provenance, and publish to PyPI by trusted publishing after approval.

### Changed

- Version set to 0.0.1.dev0, a pre-alpha placeholder release that reserves the package name on PyPI.
- Spec 0.2-draft: separate `unknown` from `gap` with a `needs_validation` level, add answer provenance and evidence coverage, define drafted answers and assisted drafting.

[Unreleased]: https://github.com/Ritvo-ai/ritvo/commits/main
