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
- `ritvo inspect --registry <platform>:<file>`: reads AWS Bedrock AgentCore and Google Vertex AI Agent Engine agent exports from files and writes `inventory.json`, with the findings `agent.no_owner` and `identity.shared_key_name`. Inventory models that reject another schema version, a missing field or an unknown field. Fictional example exports in `examples/inspect/`.
- `ritvo inspect --owners <agents.csv>`: the owner overlay. Sets `owner` and `owner_team` from an intake `agents.csv`, with `--registry` or on an existing inventory with `--inventory`, records each change in `manifest.owner_overlays`, and writes the result to `--out`. The inventory `schema_version` is now 1; a version 0 inventory is rejected with a message to run the inspector again. Example overlay in `examples/inspect/owners/`.
- Tools found in code: `ritvo inspect --code` records calls that act on the world (Kite orders, Stripe payments and refunds, email, Slack and Twilio messages) as tools with a proposed tier, works out which agents reach them through the project's imports, and raises `tool.destructive_unowned` for a high tier tool reached by an agent with no owner or by no known agent. It also finds agents that call a model API over plain HTTP. Inventory `schema_version` is now 2; a version 1 inventory is rejected with a message to run the inspector again.
- `ritvo inspect --code <dir>`: finds agents in Python source, one per file that imports an agent framework or model SDK, with the framework and the credential name it reads, and the names of environment variables from code and `.env.example` files. Source is parsed, never run; `.env` files are never opened; test and vendored folders are skipped. Adds `identity.shared_key_name` findings across every agent.
- `ritvo import-doa <matrix.csv>`: reads a delegation-of-authority matrix as the organisation keeps it, exported as CSV, in a wide or long layout. Proposes how to read it and asks to confirm each answer, saves the answers to `<name>.mapping.yaml` so later runs ask only about new rows (`--no-input` for scripts), and writes `<name>.draft.yaml` with approval bands per tool, ladders of roles, role holders from `people.csv`, unlinked decisions and problems. With `--intake`, decisions are linked to tools proposed from `tools.csv`. Never edits the matrix or `ritvo.yaml`. Fictional fixtures in `tests/fixtures/doa/`.
- `ritvo diagnose <dir>`: checks filled-in intake files and prints a summary, including agents with no owner and tools with no tier. Errors suggest fixes: mechanical fixes for column names, case and spaces in IDs, and hints for the closest fixed value, person, tool or column. `--fix` writes a corrected copy to `<dir>/corrected/` with the mechanical fixes applied and never changes the original files. A name or email address is never turned into an ID.
- `ritvo inspect --defaults` and `--config <client>:<file>`: config discovery (`docs/SPEC.md` section 7.1). Reads MCP servers from the configuration files of Claude Code, Claude Desktop, Cursor and VS Code in their well-known locations on macOS, Linux and Windows, or from files the operator names, and writes them to `mcp_servers[]`. Project files are found under `--project`. Server URLs keep scheme, host and port only, commands keep the executable name only, and `env` keeps key names only. Every location checked is listed in the manifest; unreadable files are listed and never echoed. Combines with `--registry` and `--owners`. Reads files only and never starts or contacts a server.
- The gap review's scoring contract (`docs/SPEC.md` section 8.1): `unknown` now scores `needs_validation`, a new level between `gap` and `partial`, instead of `gap`. Answers can carry a `basis` (`derived`, `observed` or `asserted`) and an `evidence` reference. Each area shows its evidence coverage, and an area whose answers are all asserted is capped at `partial`. The answers file is now `review_version: 1`; a version 0 file is still read, with a message, and is not rewritten.
- `ritvo inspect --report`: writes `report.html` beside the inventory, built from the inventory alone. One self-contained file with inline styles and script, the system font stack and no network requests. Four views (platform engineer, security, policy owner, auditor) and an all-views print mode, findings ranked by reach with what was seen, why it matters and the next step, unknown shown as unknown, a label and a shape on every status, light and dark themes, keyboard-operable tabs, and a starter control mapping. `--inventory <file> --report` renders a report from an existing inventory without changing it. The comparison with a previous scan, PDF and redaction are not built yet.
- Runtime dependencies Jinja2, Pydantic and PyYAML.
- Continuous integration on Linux, macOS and Windows: lint, type check, tests, licence check, secret scan, wheel build and install test, CodeQL, and commit hygiene.
- Release workflow: on a `v*` tag, test, build, check that the tag matches the package version, attest build provenance, and publish to PyPI by trusted publishing after approval.

### Changed

- Version set to 0.0.1.dev0, a pre-alpha placeholder release that reserves the package name on PyPI.
- Spec 0.2-draft: separate `unknown` from `gap` with a `needs_validation` level, add answer provenance and evidence coverage, define drafted answers and assisted drafting.
- Spec 0.15-draft: add a `documented` basis to answer provenance, ranked below `observed` and `derived`, and cap an area at `partial` when none of its answered questions is `observed` or `derived`. Not built yet.
- PRD 0.2-draft: define phases 0 to 3 with purpose, deliverables, exit and status; add requirements for the scoring split, drafted answers, additive re-run, interview assistant, owner overlay and observe-derived proposals; correct the gap review to 17 questions. Spec 0.3-draft: add section 14 mapping phases to spec sections with build status.
- Spec 0.4-draft: define the owner overlay (`ritvo inspect --owners`), add `manifest.owner_overlays`, bump inventory schema_version to 1.
- Spec 0.5-draft: confirm the scoring contract rules. A `review_version` 0 answers file is read with `unknown` as `needs_validation`, and an area whose answers are all `asserted` is capped at `partial`.
- Spec 0.6-draft: define config discovery (`ritvo inspect --defaults`, `--config`, `--project`) for Claude Code, Claude Desktop, Cursor and VS Code, with paths checked against each client's documentation; MCP server URLs keep scheme, host and port only.
- Spec 0.7-draft: write down the config discovery rules the first build relies on (option rules, unset `APPDATA`, field parsing, manifest order and counts, strict JSON, partly unreadable files, server order, terminal output, the `--out` guard); add comments in config files to the open items; bring the phase 0 build status up to date.
- PRD draft v0.4: bring the phase 0 status up to date with what is built.
- Spec 0.8-draft: agree and define the help text and remedy hints for registry exports (section 7.3); `ritvo inspect` no longer described as needing `--registry`. PRD draft v0.5: export help moved to built.
- Spec 0.9-draft: define `ritvo diagnose <dir>` (section 8.2): the intake check and summary, suggested fixes and the corrected copy written by `--fix`. PRD draft v0.6: the intake wiring moved to built.
- Spec 0.10-draft: define the delegation-of-authority import (section 8.4) and approval bands per tool in `ritvo.yaml` (section 4.1). PRD draft v0.7: DoA import specified.
- Spec 0.11-draft: the DoA import rules the first build relies on (section 8.4). PRD draft v0.8: DoA import built.
- Spec 0.12-draft: define the code and environment scan (section 7.5). PRD draft v0.9: code scan built.
- Spec 0.13-draft: tools found in code, reach, HTTP model calls, inventory schema_version 2, and `tool.destructive_unowned` (sections 4.8, 7, 7.4, 7.5). PRD draft v0.10.

[Unreleased]: https://github.com/Ritvo-ai/ritvo/commits/main
