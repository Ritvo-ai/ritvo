# Ritvo

Ritvo is an open-source authority layer for AI agents. When an agent asks to do something its bindings do not clearly allow, Ritvo decides who may approve it, escalates along named roles, enforces separation of duties, and keeps the evidence in a tamper-evident ledger.

The name comes from the Sanskrit *ṛta*, the right order of things.

## Status

**Early, pre-alpha.** The `ritvo` package on PyPI (version 0.0.1.dev0) is a placeholder that reserves the name. It contains only the `ritvo --version` command and no features. Nothing here is ready for production use. Interfaces, file formats and commands will change without notice until the first feature release, v0.1. See [CHANGELOG.md](CHANGELOG.md) for what exists today.

## Where Ritvo sits

Gateways sit between an agent and its tools. Ritvo sits below them, next to the resource, and above them as the policy authority:

- **Enforcement points** run next to the resource. The first is middleware inside your own MCP server, added with one library call and removed the same way. They work behind any gateway, or none.
- **The Governance OS** publishes signed policy bundles to enforcement points, resolves asks along escalation ladders, and mints single-use grants. It never touches a live call.
- **The inspector** (`ritvo inspect`) is a local, read-only scan of the MCP servers and agent frameworks you name. It writes an inventory and a report; it sends nothing anywhere.
- **The diagnostic** (`ritvo review`, `ritvo diagnose`) reads files and writes a gap report and a draft `ritvo.yaml`. It never connects to anything.

The design is in [docs/PRD.md](docs/PRD.md) (what and why) and [docs/SPEC.md](docs/SPEC.md) (behaviour and interfaces). Why Ritvo is not a gateway is recorded in [docs/decisions/0001-below-the-gateway.md](docs/decisions/0001-below-the-gateway.md).

## Non-goals

- **Not a gateway.** No key issuance, budgets, provider proxying or tool allow lists. Those are well served elsewhere.
- No credential custody, no TLS interception, no autonomy scoring, no agent-authored intent.
- Not multi-tenant SaaS. One instance per organisation, plus the library inside that organisation's own MCP server.
- No vertical policy content.
- Not a security scanner for prompt injection or malicious tool descriptions.

## Quick start

The first working command is the authority gap review. To install from source:

```sh
git clone https://github.com/Ritvo-ai/ritvo.git
cd ritvo
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
ritvo --version
```

### Authority gap review

Answer 17 questions about how your organisation governs its agents, then score them:

```sh
cp examples/review/answers.yaml my-answers.yaml   # edit the answers
ritvo review my-answers.yaml
```

`ritvo review` prints one line per area and writes `review.html` beside the answers file: a one-page, self-contained result that works offline and prints on one page. It reads the one file and connects to nothing. Keep real answers out of public repositories. The questions and scoring are in [docs/SPEC.md](docs/SPEC.md) section 8.1.

### Intake templates

Record your agents, the tools they use and the people who own and approve them, for `ritvo diagnose`:

```sh
ritvo diagnose --templates intake/   # writes blank agents.csv, tools.csv and people.csv
```

Fill them in with a spreadsheet; `examples/intake/` shows them filled in for a fictional organisation. An empty cell means not known. The command never overwrites a file. Filled-in templates are client data: keep them out of public repositories. The columns are in [docs/SPEC.md](docs/SPEC.md) section 8.2. How the templates fit with the other commands is in the workflow below.

## Workflow

Every command below reads files and writes files. None connects to a platform or an MCP server, and nothing is sent anywhere. Inventories, intake files and answers for a real organisation are client data: keep them out of public repositories. Each command prints what it wrote. Run `ritvo <command> --help` for every option.

The steps run in this order. Step 4 is optional.

**1. Find what exists.** `ritvo inspect` reads one or more sources and writes `inventory.json`, with a manifest of every file it read. Sources can be combined in one run:

```sh
ritvo inspect --defaults                                      # MCP servers in the configs of Claude Code, Claude Desktop, Cursor and VS Code
ritvo inspect --config cursor:path/to/mcp.json                # one config file
ritvo inspect --code ./my-agents                              # Python source: agents, the tools they call, environment variable names
ritvo inspect --registry agentcore:agentcore-runtimes.json    # a platform's agent export
```

Source is parsed, never run. `.env` files are never opened and environment values are never read. For a platform export, `ritvo inspect --help` lists the command that produces the file. The run prints how many agents have no owner, how many credential names are shared, and how many high tier tools are reached by an agent with no owner. The format is in [docs/SPEC.md](docs/SPEC.md) section 4.8 and the sources are in section 7.

Add `--report` to any of these runs to also write `report.html` beside the inventory. It is built from the inventory alone and is one file that opens offline: a view each for the platform engineer, security, the policy owner and the auditor, and an all-views mode for printing. To make a report from an inventory you already have, run `ritvo inspect --inventory inventory.json --report`; the inventory is not changed. The report is described in [docs/SPEC.md](docs/SPEC.md) section 7.2.

**2. Record who owns what.** Write the blank intake templates, fill them in, then check them:

```sh
ritvo diagnose --templates intake/    # blank agents.csv, tools.csv, people.csv
ritvo diagnose intake/                # counts, and a suggested fix for each error
ritvo diagnose intake/ --fix          # writes a corrected copy to intake/corrected/
```

`--fix` applies mechanical fixes only and never changes your files. The `agent_id` values in `agents.csv` must match the IDs in the inventory.

**3. Merge the owners into the inventory.**

```sh
ritvo inspect --inventory inventory.json --owners intake/agents.csv --out inventory-owned.json
```

The inventory you name is never changed. Each owner set from the file is recorded in the manifest, and a row that matches no agent is reported and not used. The findings are worked out again.

Add `--report` to write a report from the merged inventory.

**4. Optional: read a delegation-of-authority matrix.** Export your own matrix as UTF-8 CSV, in a wide or long layout:

```sh
ritvo import-doa matrix.csv --intake intake/
```

It proposes how to read the sheet and asks you to confirm each answer, saves the answers to `matrix.mapping.yaml`, and writes `matrix.draft.yaml` with approval bands per tool and the roles that hold them. Later runs ask only about new rows; `--no-input` asks nothing. It never edits the matrix or any policy file. See [docs/SPEC.md](docs/SPEC.md) section 8.4.

**5. Score the result.** Answer the 17 questions and run the gap review, as in the first section above:

```sh
ritvo review my-answers.yaml
```

The answers are typed by hand for now.

To try steps 1 to 3 on fictional data from a clone:

```sh
ritvo inspect --registry agentcore:examples/inspect/agentcore-runtimes.json --report
ritvo inspect --inventory inventory.json --owners examples/inspect/owners/agents.csv --out inventory-owned.json
ritvo diagnose examples/intake
```

The first command writes `inventory.json` and `report.html`; open the report in a browser. The example owners file has one row that matches no agent, so the second command prints one warning.

**Not built yet:** the draft `ritvo.yaml` and the gap report from `ritvo diagnose`, answers drafted from an inventory, `ritvo init`, `ritvo import`, comparing a report with a previous scan, PDF and redacted reports, and everything that enforces or approves (`ritvo demo`, `ritvo approve`). The phases are in [docs/PRD.md](docs/PRD.md) section 7.

## Getting help

See [SUPPORT.md](SUPPORT.md). Report security issues privately as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every commit needs a DCO sign-off. Please read the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The name Ritvo is covered by [TRADEMARK.md](TRADEMARK.md).
