# Ritvo — Technical Specification

Version 0.1-draft · 13 October 2026 · Licence: Apache-2.0

The words MUST, SHOULD and MAY are used as in RFC 2119. Items marked (verify) are unconfirmed and must be checked before they are relied on. Where this spec and any other project document differ, this spec wins; raise the difference rather than guess.

## 1. Scope

This spec defines the behaviour and interfaces of Ritvo v0.x: the Governance OS, the enforcement side (MCP-server middleware and the mock control plane), the adapter contract, the inspector, the diagnostic toolkit, the ledger and the CLI. It does not define a gateway, credential custody, a hosted service or vertical policy content.

## 2. Terms

| Term | Meaning |
| --- | --- |
| Principal chain | Who a request is for and through whom: subject, actor, hops, scopes, mode, owner |
| Subject | The end user on whose behalf an agent acts; may be absent |
| Actor | The agent making the call |
| Owner | The registered human or team accountable for an agent |
| Accountable human | The subject if present, otherwise the owner |
| Boundary | Where identity is handed off: `edge`, `mcp` or `data` |
| Enforcement point | Code next to a resource that evaluates a bundle and enforces the result |
| Tool | An upstream plus an action pattern, for example `broker.order` = `POST /orders/*` |
| Binding | agent × tool → `allow`, `ask` or `deny`, with limits, a tier and an owner team |
| Tier | `low`, `medium` or `high` risk |
| Ask | A request that is neither a clean allow nor a deny and needs a human decision |
| Grant | A signed, single-use, fingerprint-bound approval |
| Fingerprint | SHA-256 over the canonical form of the accountable human, actor, tool, action and parameters |
| Bundle | A signed, versioned set of compiled policy that an enforcement point evaluates locally |
| Inventory | A file describing the agents, tools, MCP servers and identity settings found in an estate |

## 3. Components and boundaries

| Package | Responsibility |
| --- | --- |
| `governance_os` | routing, escalation, roles, grants, precedent, policy change, authority diff, metrics |
| `enforcement/mcp_middleware`, `identity`, `bundles` | token validation, principal chain, local bundle evaluation, grant checks |
| `mock_control_plane` | test harness only: registry, binding check, hold and release, failure injection |
| `adapter_contract` | message schemas, versioning, signing, idempotency |
| `adapters` | one folder per attachable gateway; `mock` first |
| `connectors/registry` | read-only import of a client's agent directory or exports |
| `inspector` | local read-only scan that writes `inventory.json` |
| `diagnostic` | gap review and diagnostic over files; never connects |
| `ledger`, `secrets`, `config`, `cli`, `ui` | shared infrastructure |

Rules:

- `governance_os` and the enforcement side (`enforcement`, `mock_control_plane`, `adapters`) MUST NOT import each other's internals. They share `adapter_contract`, `ledger`, `secrets` and `config`.
- `inspector` MUST NOT import `governance_os`. It writes a file; `ritvo import` reads it.
- `diagnostic` MUST NOT make network calls.
- The Governance OS never touches a live call. It publishes bundles, resolves asks and mints grants.

## 4. Data model

Examples are shown in YAML for readability. The wire format for messages, bundles, grants and inventories is JSON.

### 4.1 ritvo.yaml

```yaml
mode: observe                  # observe | enforce
profile: local                 # local | cloud
enforcement:
  - id: demo-mcp
    type: mcp_middleware       # mcp_middleware | mock | adapter:<name>
    boundary: mcp              # edge | mcp | data
identity:
  issuers:
    - name: corp-idp
      jwks_url: secret://corp-idp/jwks
      audience: demo-mcp
  claims_map: { subject: sub, actor: act.sub, scopes: scope }
  unattended: require_owner    # require_owner | allow
secrets:
  backend: keyring             # keyring | env_file | memory
agents:
  - id: acme.research
    owner: alice
    owner_team: research
tools:
  - id: broker.order
    match: { method: POST, path: /orders/* }
    tier: high
    approval_ttl_minutes: 15
bindings:
  - agent: acme.research
    tool: broker.order
    decision: ask              # allow | ask | deny
    limits: { max_order_value: 50000 }
    owner_team: risk
policy_sections:
  - id: trading
    signoff: [risk, security]
roles:
  policy_author: alice
  escalation_reviewer: bob
  cool_off_minutes: 60
ladders:
  high: [category_owner, finance]    # ordered roles; each with a timeout
  timeout_minutes: 30
```

Constraints: every agent MUST have an `owner` when `mode` is `enforce`. Config values that are secrets MUST be `secret://` references. `ritvo check` MUST reject a file that violates these.

### 4.2 Principal

```yaml
principal:
  mode: attended               # attended | unattended
  subject: { id: u-1842, issuer: corp-idp }   # null when unattended
  actor: { id: acme.research, platform: agentcore }
  owner: alice
  chain:
    - boundary: edge
      audience: agent-platform
      scopes: [orders:read, orders:write]
      token_ref: sha256:9b1c...
    - boundary: mcp
      audience: demo-mcp
      scopes: [orders:read, orders:write]
      token_ref: sha256:41e7...
```

- Scopes MAY narrow along `chain` and MUST NOT widen. A widening hop is flagged, logged and handled per tier.
- `token_ref` is a hash or identifier. A raw token MUST NOT be stored, logged or forwarded.
- In `unattended` mode `subject` is null and `owner` MUST be set, or the request is refused when `identity.unattended` is `require_owner` in enforce mode.

### 4.3 Binding evaluation

Evaluation order for a request after the principal is built:

1. Match the tool by upstream and action pattern. No match: treat as an unknown tool (observe: allow and log as a registration candidate; enforce: deny and emit `call.denied`).
2. Find bindings for the actor and tool. None: an authority gap (observe: allow and log; enforce: deny and emit `call.denied`).
3. Apply `deny` first, then parameter limits, then `ask`, then `allow`. A parameter beyond a limit becomes an ask for `medium` and `high` tiers and a deny for `low`.
4. Evaluate ask-triggers (section 5.3).

### 4.4 Fingerprint

`fingerprint = SHA-256(JCS(accountable_human, actor.id, tool, action, parameters))`, where JCS is RFC 8785 JSON canonicalisation. The same agent acting for two users MUST produce two fingerprints.

### 4.5 Grant

A compact JWS signed with Ed25519. Header: `alg: EdDSA`, `kid`. Payload:

```yaml
grant_id: g-7f3a
request_fingerprint: sha256:ab12...
principal_digest: sha256:77de...
tool: broker.order
parameter_hash: sha256:0c9d...
approver: bob
ladder_step: 1
issued_at: 2026-10-13T10:42:00Z
expires_at: 2026-10-13T10:57:00Z
audience: demo-mcp            # the enforcement point
single_use: true
```

Enforcement points hold public keys only. A grant is valid only if the signature verifies, the audience matches, the fingerprint matches the presented request, it has not expired, and it has not been used. Used grants MUST be recorded durably before the call executes.

### 4.6 Bundle

```yaml
bundle:
  policy_version: 42            # monotonically increasing integer
  issued_at: 2026-10-13T10:00:00Z
  cedar: "<compiled Cedar policy text>"
  tools: [ { id: broker.order, tier: high, approval_ttl_minutes: 15 } ]
  bindings: [ { agent: acme.research, tool: broker.order, decision: ask, limits: { max_order_value: 50000 } } ]
  fail_modes: { low: open, medium: closed, high: closed }
  max_age_minutes: 60
signature: "<detached signature by the Governance OS key>"
```

An enforcement point MUST verify the signature and MUST refuse a bundle with a lower `policy_version` than the one it holds. A bundle older than `max_age_minutes` is stale (section 5.6).

### 4.7 Ledger event

Fields: `event_id`, `timestamp`, `principal` (subject, actor, chain, mode), `boundary`, `tool`, `action`, `parameter_fingerprint`, `policy_version`, `binding_id`, `decision`, `triggering_rule`, `approver`, `approval_or_grant_ref`, `outcome`, `latency_ms`, `prev_hash`, `hash`.

`hash = sha256(prev_hash || JCS(event_without_hash))`. The store MUST reject UPDATE and DELETE (SQLite triggers). The ledger is tamper-evident, not signed. Secrets and personal data MUST be redacted before writing.

### 4.8 Inventory

```yaml
schema_version: 0
generated_at: 2026-10-13T09:30:00Z
tool: ritvo-inspect 0.1
manifest:                       # exactly what was read
  paths: [ ./services/agent-a ]
  files_read: 41
  servers_contacted: [ demo-mcp ]
  env_names_seen: [ ANTHROPIC_BASE_URL ]   # names only, never values
agents:
  - id: acme.research
    source: code                # code | config | registry | middleware
    framework: langgraph        # when detected
    owner: null                 # unknown until assigned
mcp_servers:
  - id: demo-mcp
    transport: http             # http | stdio
    auth:
      protected_resource_metadata: true
      authorization_servers: [ https://idp.example.com ]
      scopes_supported: [ orders:read, orders:write ]
tools:
  - id: demo-mcp/place_order
    server: demo-mcp
    description_hash: sha256:55aa...
    input_schema_hash: sha256:c01e...
    annotations: { readOnly: false, destructive: true, idempotent: false, openWorld: false }
    proposed_tier: high         # proposal only
identity:
  issuers: [ https://idp.example.com ]
findings:
  - rule: agent.no_owner
    subject: acme.research
  - rule: mcp.no_auth
    subject: demo-mcp
```

An inventory MUST NOT contain secret values, raw config text, tokens or prompts.

## 5. Behaviour

### 5.1 Request evaluation at the middleware

1. Validate the inbound token: issuer is configured, audience equals this enforcement point, not expired. Failure: refuse with 401. A token meant for another audience, or an upstream token forwarded unchanged, MUST be refused.
2. Map claims through `identity.claims_map` and build the principal. Set `mode` from the presence of a subject.
3. If `mode` is `unattended` and no owner is registered: refuse in enforce mode; log in observe mode.
4. Evaluate the local bundle (section 4.3). Outcomes: allow, deny, ask.
5. Allow: execute; emit `ledger.entry`.
6. Deny: refuse with 403; emit `call.denied`.
7. Ask: if the request carries a valid grant, mark it used, execute, emit `grant.used`. Otherwise refuse with the error body in 5.8, emit `ask.requested`.
8. In observe mode every outcome other than a token failure executes and is logged with the decision that enforce mode would have made.

### 5.2 Ask lifecycle

| State | Entered when | Leaves to |
| --- | --- | --- |
| `requested` | `ask.requested` accepted | `pending` |
| `pending` | A ladder step is notified | `approved`, `rejected`, next step on timeout, `expired` |
| `approved` | An allowed approver resolves it; a grant is minted | `used`, `expired` |
| `rejected` | An approver rejects it | terminal |
| `used` | The grant is accepted by an enforcement point | terminal |
| `expired` | The last step times out, or `approval_ttl_minutes` passes | terminal |

Duplicate `ask.requested` events with the same fingerprint join the existing ask. A request with changed parameters or a different tool is a new ask, linked to the first. A late approval is refused and recorded.

### 5.3 Tiers and ask-triggers

| Tier | Rule |
| --- | --- |
| `low` | Act; single approver if asked; no cool-off |
| `medium` | Policy author and reviewer MUST differ; one person holding both roles gets a cool-off between authoring and reviewing |
| `high` | Always ask; the approver records a written rationale, stored as precedent after review |

Ask-triggers are rule-based: authority gap, ambiguity, low confidence with high stakes, novelty (no matching precedent), goal conflict. Tier and novelty are built first; the others are rule hooks.

### 5.4 Separation of duties

- The accountable human, the agent's owner and the approver are compared, not agent identifiers.
- An agent's owner MUST NOT approve that agent's escalated action above the low tier.
- When a subject is present, the subject MUST NOT approve their own request above the low tier.
- Where one person holds both author and reviewer roles, `cool_off_minutes` applies.

### 5.5 Policy change and authority diff

A change to `ritvo.yaml` goes through author, sign-off, version and log. A change touching bindings with an `owner_team` needs sign-off from each owning team, at the tier of the most sensitive tool it touches. The authority diff compares compiled policy and flags:

1. new agent × tool reach
2. a raised parameter limit or spend cap
3. a tier downgrade
4. a removed ask
5. a new upstream host
6. a widening of scopes a user's agent can use on the user's behalf

The same check runs in `ritvo check`. A denied call records a proposed binding that a policy author can promote to a change request in one action; it is system-generated, never agent-authored.

### 5.6 Fail modes

| Condition | Low | Medium | High |
| --- | --- | --- | --- |
| No bundle held | open if configured, else closed | closed | closed |
| Bundle stale | open if configured, else closed | closed | closed |
| Decision service unreachable, no valid grant | open if configured, else closed | closed | closed |
| Valid unexpired grant, decision service unreachable | execute | execute | execute |
| Used-grant store unavailable | closed | closed | closed |

### 5.7 Modes

`observe` passes and logs everything and records what enforce would have decided. `enforce` applies decisions. The client switches modes; a new install starts in `observe`.

### 5.8 Refusal contract

Over HTTP transports an ask is refused with status 403 and a JSON body:

```yaml
error: authority_required
escalation_id: esc-31c2
request_fingerprint: sha256:ab12...
expires_at: 2026-10-13T10:57:00Z
retry: true
```

Over MCP tool results the same body is returned as an error result (verify the exact error shape against the MCP SDK in use).

## 6. Interfaces

### 6.1 Adapter contract v0.2

Every message carries `contract_version`, `event_id` (idempotency key), `timestamp` and an author or decider. Enforcement point to Governance OS:

- `ask.requested`: `request_fingerprint`, `principal`, `boundary`, `tool`, parameters or a parameter hash with a body reference, `policy_version`, `trace_id` if available
- `call.denied`: `principal`, `boundary`, `tool`, parameters seen, `binding_id`, reason
- `grant.used`, `request.executed`, `request.expired`: fingerprint and outcome
- `inventory.reported`: an inventory fragment from the middleware at start-up
- `limit.near`: only where the enforcement point enforces limits

Governance OS to enforcement point:

- `policy.published`: the bundle
- `decision.resolved`: fingerprint, outcome (approve, reject, expire), approver, rationale reference, and for approvals a signed grant

Both directions: `ledger.entry`, joined to any gateway-side record by `event_id` and fingerprint.

A gateway adapter is supportable only if it can: identify each agent and, when present, the user; evaluate a compiled bundle locally or route a non-allow to the decision service; accept a signed, fingerprint-bound, expiring grant, or hold and release a request; accept policy from outside with a version; and emit events for asks, denials and outcomes.

### 6.2 Decision service HTTP API

| Endpoint | Purpose | Auth |
| --- | --- | --- |
| `POST /v1/events` | Accept adapter messages | Enforcement-point HMAC |
| `GET /v1/bundles/latest` | Fetch the current bundle; supports `ETag` | Enforcement-point HMAC |
| `GET /v1/keys` | Public keys for grant and bundle verification | Public |
| `GET /v1/asks/{id}` | Read an ask | Approver session |
| `POST /v1/asks/{id}/resolve` | Approve or reject | Approver session with passkey |

Enforcement-point requests carry `Ritvo-Key-Id`, `Ritvo-Timestamp` and `Ritvo-Signature` (HMAC-SHA256 over method, path, timestamp and body hash). Requests with more than five minutes of clock skew MUST be refused. Presenting a grant: header `Ritvo-Grant: <jws>`, or request metadata over MCP (verify).

### 6.3 CLI

| Command | Purpose |
| --- | --- |
| `ritvo review <answers.yaml>` | One-page authority gap result |
| `ritvo diagnose` | Draft `ritvo.yaml`, authority matrix, gap report from files |
| `ritvo inspect` | Local scan; writes `inventory.json` |
| `ritvo import <inventory.json>` | Create a draft change request from an inventory |
| `ritvo import-doa <matrix.csv>` | Draft bindings and roles from a delegation-of-authority matrix |
| `ritvo init [--questionnaire]` | Build `ritvo.yaml` |
| `ritvo check` | Validate config, secrets, identity and the authority diff |
| `ritvo apply` | Apply a signed-off policy version |
| `ritvo approve <id>` | Resolve an ask |
| `ritvo audit verify`, `ritvo audit export` | Recheck the hash chain; write JSONL |
| `ritvo demo` | Run the demo MCP server end to end |

## 7. Inspector

Purpose: give anyone a fast, local, read-only answer to what agents and tools exist and who owns them, and feed that into Ritvo as a proposal.

Position: `inspector` is a separate package. The diagnostic never connects; the inspector may connect to MCP servers the operator names, only when told to. Its output is a file the rest of Ritvo reads.

Sources in v0:

| Source | What is read | Connects? | Phase |
| --- | --- | --- | --- |
| MCP configuration files | Server entries (name, transport, command or URL) in operator-named paths | No | 0 |
| Code and environment scan | Framework imports and the names of environment variables such as `*_BASE_URL` and `*_API_KEY`, never values, in operator-named directories | No | 0 |
| Registry export | A client platform's agent export file, mapped by an adapter in `connectors/registry` | No | 0 |
| MCP introspection | `initialize` and `tools/list` on a named server; tool annotations; for HTTP servers the OAuth protected resource metadata (RFC 9728) | Yes, with consent | 1 |
| Middleware self-report | The middleware sends `inventory.reported` at start-up | Already inside the client | 2 |

Rules:

- Read-only and local. No telemetry. No network traffic except to servers the operator names.
- The manifest in the output lists every path read and every server contacted.
- Never read environment values or print secrets. Config files are parsed in memory; values matching secret patterns are dropped before anything is written.
- Spawning a stdio MCP server runs the command in the config file. The inspector MUST NOT do so unless the operator passes `--connect` and confirms each server; it SHOULD suggest running in a sandbox.
- Tier proposals come from MCP annotations: destructive → `high`, read-only → `low`, otherwise `medium`. They are proposals, never decisions.
- Findings are inventory facts, not security verdicts. Rules in v0: `agent.no_owner`, `mcp.no_auth`, `tool.destructive_unowned`, `identity.shared_key_name`.

Feeding Ritvo: `ritvo import inventory.json` creates a draft change request containing new agents (owner unassigned blocks approval), tools with proposed tiers, and no bindings. It goes through the normal sign-off flow and the authority diff. It never edits `ritvo.yaml` directly. `ritvo diagnose` runs the gap rules over the draft. Re-running the inspector and importing again produces a diff of what changed.

Relationship to security scanners: tools such as Cisco's mcp-scanner (Apache-2.0) and Invariant's mcp-scan look for malicious tool descriptions and vulnerabilities, and some send tool descriptions to a remote service. The inspector answers a different question, authority, and runs fully local. A later version MAY ingest their reports as findings.

### 7.1 Default discovery locations

Operator-named paths always work. `ritvo inspect --defaults` additionally checks the well-known locations below for the current OS. It lists every location it checked in the manifest, found or not. All paths are (verify) against each tool's current documentation before each release, and each has a fixture test.

| Client | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Project `.mcp.json` (Claude Code) | `<repo>/.mcp.json` | same | same |
| Claude Code user config | `~/.claude.json` | same | `%USERPROFILE%\.claude.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | `~/.config/Claude/claude_desktop_config.json` (unofficial builds only) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json`, `<repo>/.cursor/mcp.json` | same | `%USERPROFILE%\.cursor\mcp.json` |
| VS Code | `~/Library/Application Support/Code/User/mcp.json`, `<repo>/.vscode/mcp.json` | `~/.config/Code/User/mcp.json` | `%APPDATA%\Code\User\mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | same | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` |
| Codex CLI | `~/.codex/config.toml` | same | `%USERPROFILE%\.codex\config.toml` |
| Gemini CLI | `~/.gemini/settings.json` | same | `%USERPROFILE%\.gemini\settings.json` |

Rules: resolve home and app-data directories through the standard library (`pathlib`, `os.environ`) and never hard-code a user name. Each reader parses its format (JSON or TOML) in memory and keeps only server name, transport, command or URL, and the names of any environment keys. A missing file is not an error. A file the inspector cannot parse is listed in the manifest as unreadable; its contents are not echoed.

### 7.2 Report

`ritvo inspect --report` writes `report.html` beside `inventory.json`. It is the first thing most people will see, so it is part of the product, not an export.

Requirements:

- The report is generated from `inventory.json` alone, plus the previous inventory when one is given for the comparison. It does not scan again.
- It is one self-contained file: inline styles, scripts and data, the system font stack, and no network requests when opened. It states the SHA-256 of the inventory it was built from.
- It opens with a cover, a one-line summary of what was found, and four readings: agents, MCP servers, tools, findings. Below that a role switcher shows one view per reader, plus an all-views mode for print.
- Findings carry a priority and three fields: what was seen, why it matters, and the next step. Priority ranks by what an agent or server can reach and is not a measure of exploitability. The report says so on the page.
- Unknown is never shown as safe. A server that was not inspected, a tier that cannot be proposed and a tool list that is missing are shown as unknown, with the reason.
- Colour never carries meaning alone: every status has a label and a shape. Text meets WCAG AA contrast in light and dark. Tabs work from the keyboard.
- Print: a print stylesheet uses the light palette and puts each view on its own page. `ritvo inspect --pdf` renders the same file to PDF with a headless Chromium when one is installed (optional extra `ritvo[pdf]`). Without it, the user prints from a browser.
- `--redact` replaces agent, owner, server and tool names with stable short hashes and drops paths and commands, so a report can be shared outside the team.
- The report never contains secret values, raw configuration text, tokens, prompts or full tool descriptions (hashes only).

Views and what each one holds:

| View | Reader | Contents |
| --- | --- | --- |
| Platform engineer | Wires in the middleware | Tools per server by proposed tier; servers with transport, authorization and a suggested order; every tool with its annotations; the files each fact came from; next commands |
| Security | Checks exposure | Findings with priority; servers that run local code and whether the scan started them; scopes advertised by HTTP servers; credential names seen (names only) |
| Policy owner | Decides who may do what | Decisions needed with a suggested role for each; agents with owner, team and high-tier reach; an agent-by-server reach matrix |
| Auditor | Judges the evidence | Command, paths, files read and servers contacted; what the report does not show; changes since the previous scan; a starter control mapping with status; a sign-off block |

The starter control mapping covers inventory, named ownership, access control on tool servers, human oversight and logging. It is a starting point to review with a compliance lead and is not a conformity assessment. Areas the scan cannot see are marked not assessed.

Design reference: `docs/design/inspect-report-sample.html` is a sample with fictional data. It is a design target, not a template. The shipped report must not load web fonts or any remote resource.

## 8. Diagnostic

Files in, report out. Areas of the gap review: which agents exist and what they can do; who may approve what; what happens when nobody answers; how the rules change; what record exists. Scoring is rule-based with three levels per area. Gap rules over a draft `ritvo.yaml`: no named approver; owner can approve own agent; no escalation path; no approval expiry; policy change with no second person; high tier set to allow; shared keys; unattended agent with no owner.

## 9. Security requirements

- Tokens: validate issuer, audience and expiry; never store or forward a raw token; one hop, one token.
- Keys: agent keys, where issued, are stored as keyed hashes with expiry, rotation and revocation. The grant signing key lives in the secrets store; enforcement points hold public keys only.
- Services bind to localhost or a private network; admin endpoints require login.
- Approvals need an authenticated approver with a passkey. Never an unauthenticated link.
- The UI uses the same governed APIs as the CLI.
- Secrets are never written to logs, the ledger, config, errors or fixtures. Access is logged by name.
- Prompt injection is a known limit. The defence is structural: parameter limits and a mandatory ask on high tier.
- The repository is public: no client data, keys or answers in files, fixtures or history.

## 10. Conformance: the scenario suite

| ID | Scenario | Pass condition |
| --- | --- | --- |
| S01 | Same request retried | One held request, one approval, one execution |
| S02 | Retry with changed parameters | The original approval cannot release it |
| S03 | Reroute to a different tool | A new ask, linked to the first |
| S04 | Parallel duplicates | No double execution, no double queueing |
| S05 | Abandon | Request expires; nothing executes; ledger shows why |
| S06 | Approval after expiry | Refused and recorded |
| S07 | Requester approves own ask | Refused above the risk threshold |
| S08 | Reviewer absent | Escalates to the next role after the timeout |
| S09 | Policy change that widens reach | Diff flags it; approval tier follows the most sensitive tool |
| S10 | Outage | Low-tier reads may pass; high-tier actions fail closed |
| S11 | Owner approves their own agent's request for a user | Refused above the threshold |
| S12 | A hop widens scopes | Flagged, handled per tier, logged with the chain |
| S13 | User token expires while an ask is open | The approval cannot release; ledger shows why |
| S14 | Same agent, two users | Two requests, never folded |
| S15 | Unattended request with no registered owner | Refused in enforce mode |
| S16 | Wrong audience or forwarded upstream token | Refused |
| S17 | Grant used twice | Second use refused |
| S18 | Expired grant | Refused |
| S19 | Changed parameters or tool | Refused |
| S20 | Tampered signature | Refused |
| S21 | Decision service down after approval | A valid unexpired grant works; new asks fail per tier |

S01 to S10 apply to a control plane that holds and releases. S11 to S21 apply to an enforcement point that accepts grants. An adapter or enforcement point is called supported only when it passes every scenario that applies to it.

## 11. Versioning

- `contract_version`, bundle `policy_version`, inventory `schema_version` and the ledger format are versioned independently.
- The contract follows semantic versioning. A minor version MAY add optional fields. A receiver MUST ignore unknown optional fields and MUST reject an unknown major version.
- `policy_version` increases monotonically; enforcement points refuse a lower one.

## 12. Open items (verify)

- The Python package for Cedar evaluation, and whether it expresses a delegation chain and exposes analysis for the authority diff.
- The MCP SDK hook points for middleware, the exact error shape for a refused tool call, and how a grant rides on a request.
- Per identity provider: which claims an exchanged token carries for subject, actor and scopes.
- Whether any candidate gateway or cloud policy engine accepts an external decision hook or a signed grant.
- Launch stages of the cloud platform identity features used in the documentation.

## 13. Platform support and CI

Ritvo is a Python 3.12 package and does not depend on one operating system.

| Component | Linux | macOS | Windows |
| --- | --- | --- | --- |
| `inspector`, `diagnostic`, `ritvo` CLI | supported | supported | supported |
| Decision service, middleware, mock control plane | supported | supported | best effort until phase 4 |

CI (GitHub Actions) runs on `ubuntu-latest`, `macos-latest` and `windows-latest`:

- **Every push and pull request:** lint (ruff), type check, unit tests, `ritvo check` on the examples, the licence check, and a secret scan (gitleaks).
- **Matrix:** all three operating systems on Python 3.12, plus Python 3.13 on Linux. The inspector, diagnostic and CLI tests run on all three. The scenario suite (S01 to S21) runs on Linux and macOS, and on Windows from phase 4.
- **Discovery tests:** each OS runs the section 7.1 reader tests against fixture home directories, by overriding `HOME`, `USERPROFILE` and `APPDATA` in the test.
- **Install test:** build the wheel, install it with `pipx` in a clean environment, and run `ritvo --version` and `ritvo demo`.
- **Keyring:** CI never uses a real keyring. Tests use the in-memory secrets backend, and one contract test runs the `keyring` adapter against a fake backend.

Platform rules for contributors:

- Use `pathlib`. Never build paths with string concatenation or assume `/`.
- Spawn subprocesses without a shell. Resolve commands with `shutil.which`, so `npx` finds `npx.cmd` on Windows.
- Add `.gitattributes` with `* text=auto eol=lf` so hashed files and fixtures do not change line endings.
- SQLite files may be locked on Windows. Close connections explicitly in tests.
- Passkeys run in the browser and do not depend on the server operating system.
