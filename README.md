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

The quick start will cover `ritvo inspect` as it lands, and `ritvo demo` on `examples/mcp-demo` after that.

## Getting help

See [SUPPORT.md](SUPPORT.md). Report security issues privately as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every commit needs a DCO sign-off. Please read the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The name Ritvo is covered by [TRADEMARK.md](TRADEMARK.md).
