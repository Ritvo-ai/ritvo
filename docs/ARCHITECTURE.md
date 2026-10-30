# Ritvo — Architecture and code map

This is a map of the code for someone who wants to read it, fix it or add to it. It describes what is on the main branch. For what Ritvo is, read the [README](../README.md) and the [PRD](PRD.md). For behaviour and file formats, [SPEC.md](SPEC.md) is the authority: where this page and the spec differ, the spec wins, and a behaviour change starts as a change to the spec (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## What exists today, and what does not

The repository is in phase 0 of the plan in [PRD section 7](PRD.md#7-phases): the inspector and the diagnostic toolkit. Everything below is that code. The enforcement side (middleware, the Governance OS, the mock control plane, the adapter contract, the ledger) is specified in [SPEC.md](SPEC.md) and not built. Its components and boundaries are in section 3 of the spec; none of those folders exist yet.

One rule from that design already shapes the code: `inspector` and `diagnostic` read files and write files. They never connect to a platform, an MCP server or the network.

## Layout

```
ritvo/
  __init__.py            package version
  cli/main.py            the `ritvo` command: review, diagnose, import-doa, inspect
  inspector/             local scan -> inventory.json
    inventory.py         the inventory format (pydantic models), load and write
    registry.py          build an inventory from registry exports; the finding rules
    config.py            read MCP servers from client config files
    code.py              scan Python source for agents, tools and env var names
    owners.py            the owner overlay: merge owners from agents.csv
    report.py            the HTML inspect report, built from an inventory alone
    templates/           report.html.j2, the report page
  connectors/registry/   readers for a platform's agent export
    common.py            the record every reader returns, shared file handling
    agentcore.py         AWS Bedrock AgentCore
    vertex.py            Google Vertex AI Agent Engine
  diagnostic/            files in, reports out
    review/              the 17-question authority gap review
    intake/              agents.csv, tools.csv, people.csv: columns, reader, templates
    doa/                 delegation-of-authority matrix -> draft approval bands
tests/                   pytest; fixtures in tests/fixtures/
examples/                fictional data for the README and the tests
docs/                    PRD, SPEC, decision records, design sample
.github/                 CI, CodeQL, commit hygiene, release
```

Every module opens with a docstring that names the spec section it implements. Start there.

## How the commands flow

| Command | Entry point | What it calls |
| --- | --- | --- |
| `ritvo review <answers.yaml>` | `cli/main.py: review` | `diagnostic/review/scoring.load_answers`, `score`, then `report.write_report` |
| `ritvo diagnose --templates <dir>` | `diagnose` | `diagnostic/intake/templates.write_templates` |
| `ritvo diagnose <dir> [--fix]` | `diagnose` | `diagnostic/intake/reader.load_intake`, `fix_intake` |
| `ritvo import-doa <matrix.csv>` | `import_doa` | `diagnostic/doa/matrix` (read and propose), `mapping` (confirm and save), `draft` (build and write) |
| `ritvo inspect ...` | `inspect` | see below |

`ritvo inspect` builds one inventory from any mix of sources, in a fixed order:

1. `--registry`: `inspector/registry.inspect_registry` calls the platform readers in `connectors/registry/` and produces the first inventory.
2. `--inventory`: instead of 1, `inspector/inventory.load_inventory` reads an existing one (only valid with `--owners` or `--report`).
3. `--defaults` and `--config`: `inspector/config.read_configs`, then `add_servers`, put MCP servers into `mcp_servers[]`.
4. `--code`: `inspector/code.scan_code`, then `add_agents`, put agents, tools and environment names in.
5. `--owners`: `inspector/owners.read_overlay`, then `apply_overlay`, set `owner` and `owner_team` and work the owner-dependent findings out again.
6. `inventory.write_inventory` writes `inventory.json`, and the command prints the finding counts.
7. `--report`: `inspector/report.write_report` renders `report.html` beside the inventory. The report is rendered from the inventory file as read back from disk, and carries that file's SHA-256. With `--inventory` and no `--owners`, steps 3 to 6 are skipped and only the report is written (`--out` is refused in that case).

Each step returns a new inventory. The models are frozen, so a step copies and changes rather than editing in place.

`inspector/report.py` works in two parts: `build` turns an inventory into plain data for the page (priorities, server and tool rows, control mapping statuses, decisions), and `render_html` passes that to the template. Rules about what the report says belong in `build`; markup and style belong in the template. SPEC 7.2 lists the rules.

## How the packages depend on each other

```
cli/main.py ──► inspector/* ──► connectors/registry/*
     │               │                  │
     │               └──► diagnostic/intake   (owners.py reads agents.csv)
     │
     └──► diagnostic/{review, intake, doa}
                          doa ──► intake
```

- `inspector/inventory.py` is the leaf: models and the ID rule. `connectors/registry/common.py` imports the ID rule from it; nothing there imports `inspector/registry.py`, so there is no import cycle.
- `inspector/owners.py` is the one place the inspector uses the diagnostic: it reads `agents.csv` through `diagnostic/intake`.
- `inspector/report.py` reads the models in `inspector/inventory.py` and the export commands in `inspector/registry.py` (`EXPORTS`). Nothing imports it except `cli/main.py`.
- `diagnostic/review` stands alone. `diagnostic/doa` builds on `diagnostic/intake`.
- Keep this direction. The design also forbids the inspector importing anything from the Governance OS, which does not exist yet.

## Conventions the code relies on

- **Models are strict.** Pydantic models set `extra="forbid"`, `strict=True` and `frozen=True`. A file with an unknown or missing field is rejected, and the error names the field. Every field is written out, with `null` meaning not known.
- **Unknown is not a gap, and not safe.** `null` and `unknown` are carried through to the reports as unknown. Nothing treats a missing value as a good one. The review scores an unknown as `needs_validation`, not as a gap.
- **Errors are module exceptions.** Each module raises its own error (`RegistryError`, `ConfigError`, `CodeError`, `OverlayError`, `InventoryError`, `IntakeError`, `DoaError`, `ReviewError`). `cli/main.py` catches them and calls `_fail`, which prints `error: ...` and `Nothing was written.` and exits 1. Validate everything before writing a file.
- **No secrets, no values.** Environment variable names, credential names and key names are recorded; values never are. URLs keep scheme, host and port only; commands keep the executable name only. `.env` files are never opened.
- **Output is deterministic.** Lists are sorted where order has no meaning, files are written as UTF-8 with LF line endings, and the clock is read only for the inventory's `generated_at` and the date on the review page.
- **Reports are one file.** HTML is built with Jinja (`autoescape=True`, `StrictUndefined`), with inline styles, the system font stack and no network request. Every status carries a label and a shape as well as a colour. This holds for both the gap review page and the inspect report. The inspect report also keeps its colour pairs at 4.5 to 1 contrast in the light, dark and print palettes, and a test checks them, so a new colour needs a pair in that test.
- **Cross-platform.** `pathlib` throughout, no shell, paths handled for Windows as well as POSIX. Config discovery takes the home folder from `Path.home()` and `APPDATA` only.
- **IDs** follow one rule (`inspector/inventory.ID_PATTERN`, SPEC 8.2): lower case, up to 64 characters, `a-z 0-9 . _ -`.

## Tests

`pytest` from the repository root. Tests run on Linux, macOS and Windows in CI, never touch the network, and use only fictional organisations and obviously fake values.

| Test file | Covers |
| --- | --- |
| `test_review.py` | gap review questions, scoring contract, the review page, `ritvo review` |
| `test_intake.py` | intake templates, reader, suggested fixes, `ritvo diagnose` |
| `test_import_doa.py` | DoA matrix reading, mapping, draft, `ritvo import-doa` |
| `test_inspect.py` | inventory models, registry exports, findings, `ritvo inspect --registry` |
| `test_inspect_config.py` | config discovery per client and operating system |
| `test_inspect_code.py` | code scan: agents, tools, reach, environment names |
| `test_inspect_report.py` | the inspect report: priorities, unknowns, control mapping, no remote resources, contrast, `--report` |
| `test_owner_overlay.py` | the owner overlay and its option rules |
| `test_check_attribution.py` | the commit-message hygiene script in `.github/scripts/` |
| `test_cli.py` | `--version` and the help screen |

Fixtures: `tests/fixtures/config/` (one file per client and location), `registry/` (valid and invalid exports per platform), `doa/` (wide and long matrices with `tools.csv` and `people.csv`). Code-scan tests write their small projects into a temporary folder instead of keeping source fixtures.

## Making a change

Run the checks in [CONTRIBUTING.md](../CONTRIBUTING.md) (`ruff check .`, `ruff format --check .`, `mypy`, `pytest`). Then:

1. If behaviour changes, change `docs/SPEC.md` first (or in the same pull request), bump its version, and add a row to its revision history. If the PRD's phase status changes, update it and its history too.
2. Add a line to `CHANGELOG.md` under Unreleased.
3. One concern per pull request, Conventional Commits, `git commit -s`. No tool or session attribution anywhere in a commit, title or description.

### Where a common change goes

| Change | Edit |
| --- | --- |
| A new finding rule | add the name to `Rule` in `inspector/inventory.py`; write the rule in `inspector/registry.py` and call it from `code.py` and `owners.py`; if it depends on owners, add its name to the list `owners.py` filters when it works findings out again; add it to `_FINDING_TEXT` in `cli/main.py`; give it text in `_WHY`, `_NEXT`, `_RULE_ORDER` and `_saw` in `inspector/report.py`, or the report fails on it; SPEC section 7 |
| A new registry platform | a module in `connectors/registry/` with `PLATFORM`, `read`, `EXPORT_NOTE` and `EXPORT_COMMANDS`; add it to `READERS` and `EXPORTS` in `inspector/registry.py`; fixtures in `tests/fixtures/registry/<platform>/`; SPEC 7.3 |
| A new MCP client config | `CLIENTS` and `default_locations` in `inspector/config.py`; a fixture file per location; SPEC 7.1 (paths are unverified until a fixture and a documented source back them) |
| A new action SDK in the code scan | `CATALOGUE` in `inspector/code.py` with its calls and proposed tier; SPEC 7.5 lists the catalogue; add a case to `test_inspect_code.py` |
| A new agent framework or model SDK | `FRAMEWORKS` or `SDKS` in `inspector/code.py`; SPEC 7.5 |
| An inventory field | the models in `inspector/inventory.py`; bump `SCHEMA_VERSION` and add the old version to `_OLD_VERSIONS`; every place that builds an inventory (`registry.py`, `config.py`, `code.py`, `owners.py`); SPEC 4.8 |
| An intake column | `diagnostic/intake/columns.py`, then `reader.py` and `templates.py`; SPEC 8.2; `examples/intake/` |
| A review question | `diagnostic/review/questions.py`; SPEC 8.1; `examples/review/answers.yaml`; the question count in the README and tests |
| A change to what the inspect report says or shows | `build` in `inspector/report.py` for the rules, `templates/report.html.j2` for the page; SPEC 7.2; a case in `test_inspect_report.py` |
| A new CLI option or command | `cli/main.py`; the command table in SPEC 6.3; the workflow in the README |

## Where to read next

- [SPEC.md](SPEC.md): section 4.8 (inventory), 6.3 (CLI), 7 (inspector), 8 (diagnostic), 14 (phase status), Appendix A (revision history).
- [PRD.md](PRD.md): section 5 requirements, section 7 phases and status.
- [decisions/0001-below-the-gateway.md](decisions/0001-below-the-gateway.md): why Ritvo sits below gateways.
- [design/inspect-report-sample.html](design/inspect-report-sample.html): the design sample for the inspect report. It is a target, not a template; it loads web fonts for preview and the shipped report does not.
