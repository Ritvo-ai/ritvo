# Ritvo — Technical Specification

Version 0.4-draft · 19 October 2026 · Licence: Apache-2.0

The words MUST, SHOULD and MAY are used as in RFC 2119. Items marked (verify) are unconfirmed and must be checked before they are relied on. Where this spec and any other project document differ, this spec wins; raise the difference rather than guess. Changes to this spec are recorded in Appendix A, which is append-only.

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

`inventory.json` is what the inspector writes and what `ritvo import`, `ritvo diagnose` and the inspect report read. It is one UTF-8 JSON object. An inventory made for a real organisation is client data and MUST NOT be committed to a public repository.

```yaml
schema_version: 1
generated_at: 2026-10-13T09:30:00Z
tool: ritvo-inspect 0.1.0
manifest:                       # exactly what was read
  paths: [ ./services/agent-a, ./exports/runtimes.json ]
  files_read: 41
  unreadable: []
  servers_contacted: [ demo-mcp ]
  env_names_seen: [ ANTHROPIC_BASE_URL ]   # names only, never values
  registry_exports:
    - platform: agentcore
      sha256: 9f2c...
      agents: 3
  owner_overlays: []            # one entry per field set by --owners (section 7.4)
agents:
  - id: acme.research
    source: code                # code | config | registry | middleware
    name: null
    platform: null
    platform_ref: null
    framework: langgraph        # when detected
    owner: null                 # unknown until assigned
    owner_team: null
    credential: null
mcp_servers:
  - id: demo-mcp
    source: config
    transport: http             # http | stdio
    url: https://mcp.example.com/mcp
    command: null
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
    agents: [ acme.research ]
  - rule: mcp.no_auth
    subject: demo-mcp
    agents: []
```

Fields. Every field listed is present; `null` means not known, never a safe default. IDs follow the rule in section 8.2, except tool IDs, which are `<server id>/<tool name>`.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1` |
| `generated_at` | string | RFC 3339 time in UTC, ending `Z` |
| `tool` | string | `ritvo-inspect` and the package version |
| `manifest.paths` | strings | Every path the operator named or the inspector checked, found or not |
| `manifest.files_read` | integer | Number of files read |
| `manifest.unreadable` | strings | Paths that could not be read or parsed; their contents are never echoed |
| `manifest.servers_contacted` | strings | IDs of MCP servers contacted; empty unless `--connect` was used |
| `manifest.env_names_seen` | strings | Environment variable names seen, never values |
| `manifest.registry_exports` | objects | One per registry export read: `platform`, the file's `sha256`, and the number of `agents` taken from it |
| `manifest.owner_overlays` | objects | One per field the owner overlay changed (section 7.4): `agent` (ID), `field` (`owner` or `owner_team`), `previous` (the value it replaced, or null) and `source_row` (the line number in `agents.csv`). Never the file's other contents |
| `agents[].id` | ID | Unique in the inventory |
| `agents[].source` | string | `code`, `config`, `registry` or `middleware` |
| `agents[].name` | string or null | Display name |
| `agents[].platform` | string or null | For example `agentcore` or `vertex` |
| `agents[].platform_ref` | string or null | The platform's own identifier, such as an ARN or resource name |
| `agents[].framework` | string or null | For example `langgraph` |
| `agents[].owner` | ID or null | The accountable person |
| `agents[].owner_team` | ID or null | The accountable team |
| `agents[].credential` | string or null | The name of the identity the agent runs as, such as a role ARN or service account. A name only, never a value |
| `mcp_servers[].id` | ID | Unique in the inventory |
| `mcp_servers[].source` | string | `config` or `middleware` |
| `mcp_servers[].transport` | string | `http` or `stdio` |
| `mcp_servers[].url` | string or null | For `http`, without query string or fragment |
| `mcp_servers[].command` | string or null | For `stdio`, the executable name only, never its arguments |
| `mcp_servers[].auth` | object or null | `null` until the server is introspected; then `protected_resource_metadata` (boolean), `authorization_servers` and `scopes_supported` (strings) |
| `tools[].id` | string | `<server id>/<tool name>` |
| `tools[].server` | ID | The `mcp_servers[].id` it belongs to |
| `tools[].description_hash`, `tools[].input_schema_hash` | string | `sha256:` and the hex digest of the canonical JSON; never the text |
| `tools[].annotations` | object | `readOnly`, `destructive`, `idempotent`, `openWorld`: each boolean or null |
| `tools[].proposed_tier` | string or null | `low`, `medium` or `high`; a proposal only |
| `identity.issuers` | strings | Token issuers seen |
| `findings[].rule` | string | One of the finding rules in section 7 |
| `findings[].subject` | string | What the finding is about: an agent, server or tool ID, or a credential or environment variable name |
| `findings[].agents` | IDs | The agents involved, possibly none |

A reader MUST reject a file with another `schema_version`, a missing field or an unknown field, and name the field. A file with `schema_version` 0 was written before `manifest.owner_overlays` existed. It is rejected with a message to run the inspector again. An inventory MUST NOT contain secret values, raw config text, tokens, prompts, tool descriptions or environment variable values.

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
| `ritvo diagnose --templates <dir>` | Write the blank intake templates |
| `ritvo inspect` | Local scan; writes `inventory.json` |
| `ritvo inspect --registry <platform>:<file>` | Read a platform's agent export into `inventory.json` |
| `ritvo inspect --owners <agents.csv>` | Merge owners from an intake file into an inventory, with `--registry` or with `--inventory <file>` (section 7.4) |
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
| Registry export | A client platform's agent export file, mapped by an adapter in `connectors/registry` (section 7.3) | No | 0 |
| MCP introspection | `initialize` and `tools/list` on a named server; tool annotations; for HTTP servers the OAuth protected resource metadata (RFC 9728) | Yes, with consent | 1 |
| Middleware self-report | The middleware sends `inventory.reported` at start-up | Already inside the client | 2 |

Rules:

- Read-only and local. No telemetry. No network traffic except to servers the operator names.
- The manifest in the output lists every path read and every server contacted.
- Never read environment values or print secrets. Config files are parsed in memory; values matching secret patterns are dropped before anything is written.
- Spawning a stdio MCP server runs the command in the config file. The inspector MUST NOT do so unless the operator passes `--connect` and confirms each server; it SHOULD suggest running in a sandbox.
- Tier proposals come from MCP annotations: destructive → `high`, read-only → `low`, otherwise `medium`. They are proposals, never decisions.
- Findings are inventory facts, not security verdicts. Rules in v0: `agent.no_owner`, `mcp.no_auth`, `tool.destructive_unowned`, `identity.shared_key_name`. `agent.no_owner` is an agent whose `owner` is null. `identity.shared_key_name` is a credential or key name used by two or more agents. The other two are defined with the sources that produce them.

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

### 7.3 Registry exports

`ritvo inspect --registry <platform>:<file>` reads an agent list that the operator exported from a client platform with that platform's own tool, and writes its agents to `inventory.json` with `source: registry`. It reads only the named files and never calls the platform. `--registry` may be given more than once. `--out` sets the inventory path; the default is `inventory.json` in the current directory. Each run writes a new inventory from the sources it is given. Until the other phase 0 sources land, `ritvo inspect` needs at least one `--registry`.

Platforms:

| Platform | Export | Accepted file |
| --- | --- | --- |
| `agentcore` (AWS Bedrock AgentCore Runtime) | `aws bedrock-agentcore-control list-agent-runtimes --output json`; optionally `aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id> --output json` for each runtime | An object with `agentRuntimes` (list output), or one runtime object with `agentRuntimeArn` at the top (get output) |
| `vertex` (Google Vertex AI Agent Engine) | The `reasoningEngines.list` or `reasoningEngines.get` REST method, saved as JSON | An object with `reasoningEngines`, a JSON array of reasoning engines, or one reasoning engine object with `name` at the top |

Mapping to `agents[]`:

| Field | `agentcore` | `vertex` |
| --- | --- | --- |
| `id` | `agentcore.` and `agentRuntimeName` in lower case | `vertex.` and the last segment of `name` |
| `name` | `agentRuntimeName` | `displayName` |
| `platform` | `agentcore` | `vertex` |
| `platform_ref` | `agentRuntimeArn` | `name` |
| `framework` | null | `spec.agentFramework` |
| `owner` | null | label `ritvo-owner` |
| `owner_team` | null | label `ritvo-owner-team` |
| `credential` | `roleArn` (get output only) | `spec.effectiveIdentity`, else `spec.serviceAccount`, else null |

Rules:

- The reader copies only the fields in the mapping. It MUST NOT copy environment variables (`environmentVariables` in AgentCore, `spec.deploymentSpec` in Vertex) or any other field, and their values never reach the inventory, the terminal or an error. Unknown fields are ignored.
- A file with a non-empty `nextToken` (AgentCore) or `nextPageToken` (Vertex) holds one page only. The inspector stops with an error saying to export every page, and writes nothing.
- The same agent in several files (the same `platform_ref`) is one agent; a field that is null in one file takes the value from another. Two different `platform_ref` values that give the same `id` are an error.
- Owners come only from the two Vertex labels. A label value that is not a valid ID is ignored, the owner stays null, and the terminal names the agent and the label. Vertex label values cannot contain `.`, so an owner named in a label needs an ID without one. AgentCore exports carry no tags, so AgentCore owners are null until assigned through `agents.csv` (section 8.2) or `ritvo import`.
- A missing `agentRuntimeArn` or `agentRuntimeName` (AgentCore), or `name` (Vertex), is an error. Errors give the file name and the agent's position in the file, never field values other than IDs, and nothing is written.
- Findings from this source: `agent.no_owner` for each agent whose owner is null, and `identity.shared_key_name` for each credential that two or more agents share, with `subject` the credential and `agents` the agents that share it.
- The manifest lists each file in `paths`, counts it in `files_read`, and adds one `registry_exports` entry per file.
- The terminal prints the number of agents read from each file, the number of findings by rule, and the path written. Exit status: 0 when the inventory is written, non-zero otherwise.

### 7.4 Owner overlay

`ritvo inspect --owners <agents.csv>` sets `owner` and `owner_team` on the agents of an inventory from an `agents.csv` file (section 8.2). Registry exports often carry no owner (section 7.3), and an agent with no owner blocks approval of an import (section 7). The overlay assigns owners without anyone editing `inventory.json` by hand.

Use:

- With `--registry`, the overlay applies to the inventory built in the same run.
- On its own, `--inventory <file>` names an existing inventory to read. That file is never changed. The result goes to `--out`.
- `--out` MUST NOT be the file named by `--inventory`. That is refused, and nothing is written.
- `--owners` needs `--registry` or `--inventory`. Without either, it is an error.

Reading the file:

- The file is read by the intake reader of section 8.2, in the format of section 8.2. No other format is accepted, and the file is named `agents.csv`.
- If `people.csv` is in the same directory, it is read too, and every `owner` MUST name a person in it.
- `owner` and `owner_team` are IDs under the rule of section 8.2. An invalid file is an error as in section 8.2, and nothing is written.
- Only `agent_id`, `owner` and `owner_team` are used. The other columns are validated as in section 8.2, and not merged.

Matching:

- A row matches the inventory agent whose `id` equals the row's `agent_id`. There is no other way to match.
- A row that matches no agent is a warning that names its line number and its `agent_id`. It is never dropped silently, and it does not stop the run.

Merging:

- A non-empty `owner` or `owner_team` sets that field on the matched agent.
- If the agent already has a different non-null value, the overlay wins.
- An empty cell never changes a field. A field that nothing sets stays null, meaning not known. It is never defaulted.
- Each field the overlay changes adds one entry to `manifest.owner_overlays` (section 4.8): `agent`, `field`, `previous` and `source_row`. A value equal to the one already there changes nothing and is not recorded.
- Each intake file read is listed in `manifest.paths` and counted in `manifest.files_read`.
- With `--inventory`, the result keeps the input's agents, MCP servers, tools, identity and manifest, adds the overlay's entries, and gets a new `generated_at` and `tool`.
- `agent.no_owner` findings are worked out again from the merged agents. Other findings are kept as they were.

The terminal prints the number of fields set, how many of them replaced a non-null value, each warning, and the path written. Exit status: 0 when the inventory is written, non-zero otherwise.

## 8. Diagnostic

Files in, report out. Areas of the gap review: which agents exist and what they can do; who may approve what; what happens when nobody answers; how the rules change; what record exists. Scoring is rule-based with four levels per area (section 8.1). Gap rules over a draft `ritvo.yaml`: no named approver; owner can approve own agent; no escalation path; no approval expiry; policy change with no second person; high tier set to allow; shared keys; unattended agent with no owner.

### 8.1 Gap review

`ritvo review <answers.yaml>` reads a file of answers to 17 fixed questions in the five areas, scores each area, and writes a one-page result. It reads one file, makes no network calls and holds no credential.

Levels, used for each question and each area:

| Level | Meaning |
| --- | --- |
| `gap` | The control is known to be missing |
| `needs_validation` | Nobody could say whether the control exists, or the answer rests on assertion with no evidence. It is not a failure and gets no credit toward `in_place` |
| `partial` | The control exists for some agents or some of the time, or informally |
| `in_place` | The control exists, is written down and applies to every agent |

An unanswered question is `not_answered`. It is never scored as `in_place`. Order from worst to best: `gap`, `needs_validation`, `partial`, `in_place`.

A known gap and an unknown are different facts. Scoring them alike would read an uninformed first pass as an accusation, so they are kept apart and the report shows how much of the result rests on evidence (see Scoring and Provenance below).

Questions. Every question also accepts `unknown`, which scores `needs_validation`. (Before version 0.2 of this spec, `unknown` scored `gap`.)

| ID | Question | `gap` | `partial` | `in_place` |
| --- | --- | --- | --- | --- |
| **Agents and what they can do** | | | | |
| `agents.inventory` | Is there a list of every AI agent in use, including those built outside the central IT or platform team? | `none` | `partial` | `complete` |
| `agents.owner` | Does every agent have a named person who is accountable for it? | `none` | `some` | `all` |
| `agents.reach` | For each agent, is it written down what it can access and do, and which of its actions are high risk? | `none` | `some` | `all` |
| `agents.new_tools` | When a new tool or capability becomes available to an agent, what happens before the agent can use it? | `automatic` | `informal` | `needs_decision` |
| **Who may approve what** | | | | |
| `approval.named` | When an agent needs approval for something outside its normal permissions, who decides? | `no_one` | `informal` | `named_role` |
| `approval.self` | Can an agent's owner, or the person who asked the agent to do the task, approve that agent's own request? | `yes` | `not_prevented` | `prevented` |
| `approval.context` | When someone approves, do they see exactly what they are approving: the agent, the person who asked the agent to do the task, the tool and the parameters? | `no` | `partly` | `yes` |
| **What happens when nobody answers** | | | | |
| `escalation.path` | If the approver does not respond, does the request move to someone else? | `no` | `ad_hoc` | `defined` |
| `escalation.expiry` | Do approvals expire, and are they tied to the one request they approve? | `no` | `some` | `all` |
| `escalation.waiting` | While a request waits for a decision, what does the agent do? | `proceeds` | `held_no_rule` | `rule_by_risk` |
| `escalation.stop` | Can an agent be suspended immediately during a run, and is it decided who may do it? | `no` | `informal` | `named_role` |
| **How the rules change** | | | | |
| `policy_change.second_person` | Does a change to an agent's permissions need approval from someone other than its author? | `no` | `sometimes` | `always` |
| `policy_change.owners` | Does a change that affects a team's systems need that team's sign-off? | `no` | `informal` | `required` |
| `policy_change.versioning` | Can you say which version of the rules was in force when a given decision was made? | `no` | `partly` | `yes` |
| **What record exists** | | | | |
| `record.decisions` | Is every approval and refusal recorded with who decided, when and why? | `no` | `partly` | `yes` |
| `record.tamper` | Could the record be changed or deleted without anyone noticing? | `yes` | `logged` | `no` |
| `record.identity` | Does the record show both the agent and the person who asked the agent to do the task, with a distinct identity for each agent? | `no` | `agent_only` | `both` |

Answer values use these option IDs. For `record.tamper`, `logged` means changes are possible but are themselves logged; `no` means the record is append-only or tamper-evident. For `agents.new_tools`, `automatic` means the agent can use a new tool as soon as it appears. For `escalation.stop`, `informal` means an agent can be stopped but nobody is named to do it.

Answers file:

```yaml
review_version: 1
organisation: Example Logistics Ltd   # optional; shown on the result
answers:
  agents.inventory: partial
  agents.owner: some
  approval.self: not_prevented
  # questions left out are not_answered
basis:                                # optional, per question; see Provenance
  agents.inventory:
    source: derived                   # derived | observed | asserted
    evidence: inventory.json sha256:<hash>   # a reference, never a value
notes:                                # optional, one line per question
  agents.owner: Finance agents have owners; support agents do not.
```

Validation: `review_version` MUST be `1`. A file with `review_version: 0` is also accepted. Every `unknown` in it is read as `needs_validation`, and the file is not rewritten. The terminal prints a message that names the file, says it was read as version 0, and says grades may improve because unknown answers no longer count as a gap. The result notes that the answers file is version 0. An unknown question ID or option ID is an error that names the valid IDs, and nothing is written. An omitted question is `not_answered`.

Scoring, per area:

1. If no question in the area is answered, the area is `not_answered`.
2. Otherwise the area takes the lowest level among its answered questions, in the order above: one gap makes the area a gap, and one `needs_validation` keeps it from being better than `needs_validation`.
3. If some questions in the area are unanswered, the area cannot be `in_place`: it is capped at `partial` and marked incomplete with the number answered.
4. If every answered question in the area has a basis of `asserted`, the area cannot be `in_place`: it is capped at `partial`, and the result says why. An answer with no `basis` is `asserted`. The cap applies to the area; each question keeps its own level.

There is no total score across areas. Each area also shows its evidence coverage: how many of its questions have a `basis` of `derived` or `observed`, out of the questions in the area. Coverage is a count, not a score.

Provenance. `basis` records where an answer came from. `derived` means read from a file or registry export, `observed` means taken from logs or records, `asserted` means a person said so. `evidence` is a reference, such as the SHA-256 of `inventory.json` and a `manifest.registry_exports` entry, and MUST NOT contain a value from a file, a credential value, or any secret. The name `basis` is used so it is not confused with `agents[].source`, which says how an agent was discovered.

A `null` in an inventory is not known. An answer drafted from a `null` is `needs_validation`, never `gap` and never a pass.

Output:

- `review.html` beside the answers file, or at `--out`. It is one self-contained file under the same rules as the inspect report (section 7.2): inline styles, the system font stack, no network requests, light and dark themes, every level shown with a label and a shape as well as colour, and a print stylesheet that fits one page.
- It shows the organisation, the date, the SHA-256 of the answers file, the five areas with their levels and evidence coverage, and for each question scored `gap`, `needs_validation`, `partial` or `not_answered`: the answer given, its basis, why it matters, and a next step. It states that the answers are self-reported and that the result is not a conformity assessment.
- It MUST NOT contain the answers file's path. Notes are shown as plain text, escaped.
- The terminal prints one line per area with its level and the path of the written file.
- Exit status: 0 when the result is written, whatever the levels; non-zero when the answers file is invalid.

### 8.2 Intake templates

Three CSV files record what an organisation has today: its agents, the tools they use, and the people who own and approve them. `ritvo diagnose` reads them, with a registry export or a delegation-of-authority matrix where available, to draft `ritvo.yaml` and run the gap rules. A file filled in for a real organisation is client data and MUST NOT be committed to a public repository.

`ritvo diagnose --templates <dir>` writes the three blank templates into `<dir>`, each one a header row only, and creates `<dir>` if it does not exist. If any of the three files already exists it writes nothing and exits non-zero. It reads no other file and makes no network calls. Filled-in examples for a fictional organisation are in `examples/intake/`.

File format, for all three files:

- UTF-8, with or without a byte order mark. Comma-separated, quoted as in RFC 4180.
- The first row is the header. Column names are exact and lower case; their order is free. An unknown or repeated column, or a missing required column, is an error that names the valid columns.
- A list cell holds values separated by `;`. Spaces around values are ignored.
- An empty cell means not known, and `unknown` means the same where a column takes fixed values. Not known is never read as a safe default: an empty `tier` is not `low`, and an empty `owner` is not an owner.
- Fixed values, including `unknown`, are matched ignoring case: `High` reads as `high`. Column names and IDs are matched exactly.
- Rows whose cells are all empty are ignored. A row with fewer cells than the header has the missing cells empty; a row with more cells than the header is an error.
- An ID is 1 to 64 characters from `a-z`, `0-9`, `.`, `_` and `-`, starting with a letter or digit. IDs are unique within their file. Team IDs and role names follow the same rule.

`agents.csv`:

| Column | Required | Meaning |
| --- | --- | --- |
| `agent_id` | yes | The agent's ID; becomes `agents[].id` |
| `name` | no | Display name |
| `platform` | no | Where the agent runs, as free text, for example `copilot-studio` or `custom` |
| `built_by_team` | no | The ID of the team that built it |
| `owner` | no | The `person_id` of the person accountable for it |
| `owner_team` | no | The ID of the team accountable for it |
| `mode` | no | `attended`, `unattended` or `both` |
| `credential` | no | The name of the credential, key or service account the agent uses to call tools. A name only, never a value |
| `tools` | no | List of the `tool_id`s the agent can use today |

`tools.csv`:

| Column | Required | Meaning |
| --- | --- | --- |
| `tool_id` | yes | The tool's ID; becomes `tools[].id` |
| `system` | no | The system or server the tool acts on |
| `access` | no | `read` or `write`. Anything that changes or deletes data, sends a message or moves money is `write` |
| `description` | no | One line on what the tool does |
| `tier` | no | `low`, `medium` or `high` |
| `owner_team` | no | The ID of the team that owns the system; becomes the `owner_team` of bindings to this tool |
| `approval_ttl_minutes` | no | A whole number of minutes, at least 1; becomes `tools[].approval_ttl_minutes` |

`people.csv`:

| Column | Required | Meaning |
| --- | --- | --- |
| `person_id` | yes | The ID used in `owner` and `deputy`. Use an ID, not an email address or full name |
| `team` | no | The ID of the person's team |
| `roles` | no | List of role names, for example `policy_author; escalation_reviewer; finance` |
| `deputy` | no | The `person_id` who acts when this person is absent; never the person themselves |

Validation:

- `owner` and `deputy` MUST name a person in `people.csv`, and `tools` MUST name tools in `tools.csv`, when that file is given. Any of the three files may be absent; references into an absent file are not checked.
- An error gives the file name, line and column, and nothing is written. An error MUST NOT echo a cell's content, except an ID or a fixed value, shortened to 40 characters.
- The `credential` column is compared across agents to find shared keys (section 8). Its value is never written to a report or to the draft `ritvo.yaml`.

### 8.3 Drafted answers

The inspector comes first. Where an inventory exists, the answers file is drafted from it, and a person confirms the draft instead of composing it.

- The draft is written as a separate file. It is never written over an answers file, and it is never scored until a person has confirmed it.
- Each of the 17 questions is routed one of three ways. **Derive**: read from files or an export. **Observe**: taken from logs or records. **Ask**: only a person can say. A question routed to a person stays `not_answered` in the draft.
- Every drafted answer carries a `basis` and an `evidence` reference. A `null` in the inventory becomes `needs_validation`.
- Routing, as a first reading from the question text (verify each against the inventory fields that exist when this is built):

| Route | Questions |
| --- | --- |
| Derive | `agents.inventory`, `agents.owner`, `agents.reach`, `escalation.expiry`, `policy_change.second_person`, `policy_change.owners`, `policy_change.versioning` |
| Observe | `agents.new_tools`, `approval.self`, `escalation.waiting`, `record.decisions`, `record.identity` |
| Ask | `approval.named`, `approval.context`, `escalation.path`, `escalation.stop`, `record.tamper` |

- The command that produces the draft, and the sources for the Observe questions, are open items (section 12). Until the inspector lands, a person writes the answers file by hand, as in section 8.1.

## 9. Security requirements

- Tokens: validate issuer, audience and expiry; never store or forward a raw token; one hop, one token.
- Keys: agent keys, where issued, are stored as keyed hashes with expiry, rotation and revocation. The grant signing key lives in the secrets store; enforcement points hold public keys only.
- Services bind to localhost or a private network; admin endpoints require login.
- Approvals need an authenticated approver with a passkey. Never an unauthenticated link.
- The UI uses the same governed APIs as the CLI.
- Secrets are never written to logs, the ledger, config, errors or fixtures. Access is logged by name.
- Prompt injection is a known limit. The defence is structural: parameter limits and a mandatory ask on high tier.
- Assisted drafting: an AI assistant MAY draft inventories and answers, interview the operator, and draft policy proposals. A draft never approves, signs, activates or publishes anything. An assistant runs as a governed agent with a registered owner and narrow scopes, writes only to a proposals area, has no approve operation, and cannot write `ritvo.yaml`, a bundle or the ledger. Its output is marked `asserted` unless it is backed by evidence. Until Ritvo's own enforcement exists, these limits are enforced in code.
- Evidence outranks assertion: a result that rests only on a person's or an assistant's statement is shown as such, and it is not scored `in_place`.
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
- Owner overlay: defined in section 7.4 (decision D8). No longer open.
- Help and errors for registry exports (proposed): `ritvo inspect --help` and the report show the commands that produce each export file, and a refusal that has a remedy says what to do without echoing a field value, for example to re-export so a partial page is whole.
- The command that drafts an answers file from an inventory, and the sources for the Observe questions in section 8.3.
- How an evidence reference to a document is recorded (section 8.1). The cap for `asserted` answers is now defined in section 8.1, scoring rule 4.
- The migration of `review_version: 0` files: defined in section 8.1. No longer open.
- Which design partner's platforms set the order of inspector sources after the registry readers.

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

## 14. Phases

The PRD (section 7) defines the phases: 0 See, 1 Observe, 2 Decide, 3 Govern, then 4 and 5. This section maps them to the sections of this spec and records status as of 19 October 2026. "Specified" means this spec defines it. "Built" means the code is on the main branch.

| Phase | Spec sections in force | Built | Specified, not built |
| --- | --- | --- | --- |
| 0 See | 4.8 inventory; 6.3 `review`, `diagnose`, `inspect`; 7, 7.1, 7.2, 7.3 inspector; 8, 8.1, 8.2, 8.3 diagnostic; 9 assisted drafting and evidence rules | `ritvo review` (8.1, before the 0.2 changes); intake templates and reader (8.2) | Inventory format; registry exports; config discovery; the scoring contract in 8.1 (`needs_validation`, coverage, `basis`); drafted answers (8.3); the report in 7.2 |
| 1 Observe | 4.2 principal; 4.3 binding evaluation; 4.4 fingerprint; 4.6 bundle; 4.7 ledger; 5.1 request evaluation; 5.6 fail modes; 5.7 modes; 5.8 refusal; 6.1, 6.2 contract and decision service; 7 MCP introspection | Nothing | All of it |
| 2 Decide | 4.5 grant; 5.2 ask lifecycle; 5.3 tiers; 5.4 separation of duties; 10 scenarios S01 to S21; 7 middleware self-report | Nothing | All of it |
| 3 Govern | 5.3 novelty and precedent; 5.5 policy change and authority diff; metrics | Nothing | All of it |
| 4 and 5 | Not yet specified beyond the platform note in 13 | Nothing | None |

Items in this spec marked (verify) are not agreed and are not built from until they are confirmed. Section 12 lists them.

## Appendix A. Revision history

Append-only. A change adds a row; earlier rows are not edited.

| Version | Date | Change | Source |
| --- | --- | --- | --- |
| 0.1-draft | 13 October 2026 | First draft | |
| 0.1-draft | 19 October 2026 | Inventory format (4.8), findings and sources table (7), registry exports (7.3), CLI table (6.3) | Pull request 11 |
| 0.2-draft | 19 October 2026 | `unknown` and `gap` separated: new level `needs_validation`, evidence coverage per area, answer provenance (`basis`, `evidence`), `review_version` 1 (8.1). Drafted answers, inspector first (8.3). Assisted drafting and evidence-outranks-assertion rules (9). Open items added (12). | Decisions of 19 October 2026: inspector first; split `unknown`; AI assistants allowed under governance. The owner overlay and the help text for exports are proposals, in section 12 only. |
| 0.3-draft | 19 October 2026 | Phases mapped to spec sections with build status (14). | Phase definitions in PRD section 7, 19 October 2026. |
| 0.4-draft | 19 October 2026 | Owner overlay defined (7.4). `ritvo inspect --owners` added to the CLI table (6.3). `manifest.owner_overlays` added and inventory `schema_version` raised to 1, with version 0 files rejected (4.8). Owner overlay closed in the open items (12). | Decision D8 |
| 0.5-draft | 19 October 2026 | Scoring contract confirmed (8.1). A `review_version` 0 answers file is accepted: `unknown` is read as `needs_validation`, the file is not rewritten, a message names it, and the result notes it. An area whose answered questions are all `asserted` is capped at `partial`, and the result says why; an answer with no `basis` is `asserted`. Both rules closed in the open items (12). | Decisions on the scoring contract |
