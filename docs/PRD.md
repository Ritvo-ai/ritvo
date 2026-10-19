# Ritvo — Product Requirements (PRD)

Status: draft v0.2 · 19 October 2026 · Owner: SK · Licence: Apache-2.0

## 1. Summary

Ritvo is an open-source authority layer for AI agents. When an agent asks to do something its bindings do not clearly allow, Ritvo decides who may approve it, escalates along named roles, enforces separation of duties, and keeps the evidence. It runs next to the resource, as middleware inside the client's own MCP server, below any gateway. An inspector and a diagnostic toolkit show a client what is running and where the authority gaps are before anything is installed.

## 2. Problem

Enforcement (registries, allow and deny rules, spend caps, audit logs) is a commodity. Every major cloud and identity vendor ships it, and so do several gateways. What is missing is the organisation's authority model at runtime: who may write the policy, who decides when an agent asks for something outside it, who the decision escalates to, and whether the requester can also approve.

The failures are organisational, not technical:

- The owner of an agent approves that agent's own access request.
- An escalation goes to the one engineer who configured the rule, who is on leave.
- A policy edit that raises a spend cap is approved at the same tier as a typo fix.
- The same borderline case is argued from scratch every time.
- A denied call leaves no trail that becomes a proposed fix, so people work around the gateway.

Evidence that the gap is common, all from vendor-run or vendor-commissioned surveys: 53% of organisations had agents exceed their intended permissions and only 15% say most agents have a defined owner (Cloud Security Alliance with Zenity, 445 respondents); 34% acted on an inaccurate agent decision and 38% let agents approve or reject transactions (Optro, 417 respondents).

What the evidence does not show: that anyone pays for a decision-rights layer, or that default-deny is blocking production work. A reviewer with cloud and identity experience said the pain is not high enough yet. This product is an early bet, built in the open, and its commercial path stays behind separate gates.

## 3. Users

| User | Need | Success looks like |
| --- | --- | --- |
| Platform or security engineer | See what agents and tools exist; add control to an MCP server without rewriting it | Inventory in minutes; one library call; reversible |
| Policy author (risk, compliance, business owner, platform) | Write and change rules across several teams | A first signed policy version without a bespoke tool |
| Approver (a named role) | Decide quickly, with context, on a phone | Cannot approve their own agent's action; sees what exactly is being approved |
| Auditor or evidence reader | Answer who authorised an action under which policy version | A verifiable ledger and a per-request record |
| Adopter or consultant | Run a gap review and diagnostic before any install | A one-page result and a draft `ritvo.yaml` |
| Contributor or gateway vendor | Implement the adapter contract | A versioned contract and a scenario suite to pass |

## 4. Goals and non-goals

Goals for v0.x:

1. Make authority visible: an inspector and diagnostic produce an inventory and a gap report.
2. Enforce decision rights at the resource, whatever gateway sits in front.
3. Keep evidence: a tamper-evident ledger tied to named people and policy versions.
4. Be easy to try: a measured time to first governed decision.
5. Stay neutral across agent platforms, identity providers and gateways.

Non-goals:

- Not a gateway: no key issuance, budgets, provider proxying or tool allow lists.
- No credential custody, no TLS interception, no autonomy scoring, no agent-authored intent.
- Not multi-tenant SaaS.
- No vertical policy content.
- Not a security scanner for prompt injection or malicious tool descriptions; other open-source tools do that.
- Voice and call-centre segments, where no human can be asked, are not served.

## 5. Requirements

Phase numbers refer to section 7. P0 is the MVP (phases 0 to 3).

| ID | Requirement | Priority | Phase |
| --- | --- | --- | --- |
| R-INS-1 | `ritvo inspect` discovers MCP server configs and agent frameworks in operator-named paths, read-only and local; `--defaults` also checks the well-known client locations (SPEC 7.1) | P0 | 0 |
| R-INS-2 | Import a client platform's agent registry export from a file | P0 | 0 |
| R-INS-3 | Introspect named MCP servers (tools, annotations, auth metadata) only with explicit consent | P0 | 1 |
| R-INS-4 | Write `inventory.json` with a manifest of what was read and no secret values | P0 | 0 |
| R-INS-5 | `ritvo import` turns an inventory into a draft change request through the normal sign-off flow; it never edits policy directly | P0 | 1 |
| R-INS-6 | The middleware reports its own inventory at start-up | P0 | 2 |
| R-INS-7 | Inventory diff and a CI check for new tools | P1 | 3 |
| R-INS-8 | `ritvo inspect --report` writes a self-contained HTML report with one view each for platform engineers, security, policy owners and auditors; `--pdf` and `--redact` | P0 | 0 (HTML), 1 (diff, PDF, redact) |
| R-INS-9 | Owner overlay: `ritvo inspect --owners agents.csv` merges owner and owner team into an inventory, so an agent with no owner in a registry export can be assigned one (SPEC 7.4) | P1 | 0 |
| R-DIA-1 | Authority gap review: 17 questions, rule-based scoring, one-page result | P0 | 0 |
| R-DIA-2 | Diagnostic: draft `ritvo.yaml`, authority matrix, gap rules, starter control mapping, report. The intake reader is called by `ritvo diagnose`, and its errors suggest fixes | P0 | 0 |
| R-DIA-3 | Scoring keeps a known gap apart from an unknown: level `needs_validation`, evidence coverage per area, answer provenance (`basis`, `evidence`), `review_version` 1 (SPEC 8.1) | P0 | 0 |
| R-DIA-4 | Drafted answers: the answers file is drafted from an inventory, a person confirms it. Derive questions in phase 0, Observe questions once observe-mode records exist (SPEC 8.3) | P0 | 0 (Derive), 1 (Observe) |
| R-DIA-5 | Additive re-run: carry an answer forward only if its source is unchanged | P1 | 0 |
| R-DIA-6 | Interview assistant: asks only the remaining questions and records answers as `asserted`. In phase 0 its limits are enforced in code; from phase 2 it runs as a governed agent (SPEC 9) | P1 | 0, 2 |
| R-SEC-1 | A machine draft never approves, signs, activates or publishes. Assistants write only to a proposals area. Evidence outranks assertion (SPEC 9) | P0 | 0 onward |
| R-ENF-1 | Validate the inbound token (issuer, audience, expiry) and build the principal chain, including `mode` | P0 | 1 |
| R-ENF-2 | Evaluate a signed Cedar bundle locally: allow, deny or ask | P0 | 1 |
| R-ENF-3 | Observe mode by default; enforce is the client's switch | P0 | 1 |
| R-ENF-4 | Refuse asks with an escalation ID; accept a signed, single-use, fingerprint-bound grant | P0 | 2 |
| R-ENF-5 | High tier fails closed; low tier may fail open if configured | P0 | 1 |
| R-ENF-6 | Never store or forward a raw token | P0 | 1 |
| R-GOS-1 | Act, ask or wait routing with ask-triggers (tier and novelty first) | P0 | 1 |
| R-GOS-2 | Escalation ladders with timeouts to the next named role | P0 | 2 |
| R-GOS-3 | Separation of duties across subject, agent owner and approver; cool-off where one person holds both roles | P0 | 2 |
| R-GOS-4 | Approvals page (passkey) and `ritvo approve` | P0 | 2 |
| R-GOS-5 | Precedent log with human review before it binds | P0 | 3 |
| R-GOS-6 | Governed policy changes: multi-team sign-off, authority diff, denial to draft change | P0 | 3 |
| R-GOS-7 | Metrics: time to decision, blocked agent-hours, override rates, approver response times | P0 | 3 |
| R-GOS-8 | Policy distribution as signed bundles | P0 | 1 |
| R-GOS-9 | Observe-derived policy proposals: machine drafts from observed traffic, disposed of by a person in batches (proposed) | P1 | 3 |
| R-GOS-10 | Pre-ask resolution: an ask is checked against standing rules and precedent before a person sees it; each rule has a named owner and expires (proposed) | P1 | 3 |
| R-LED-1 | Hash-chained append-only ledger with `audit verify` and `audit export` | P0 | 1 |
| R-DEL-1 | `ritvo demo` on `examples/mcp-demo` reaches a resolved ask (it runs in observe mode from phase 1) | P0 | 2 |
| R-DEL-2 | Scenario suite of 21 scenarios in CI; adapters must pass it | P0 | 2 |
| R-DEL-3 | Public repo hygiene: LICENSE, NOTICE, SECURITY, CONTRIBUTING, CI with licence check | P0 | 0 |
| R-LATER | Governance UI, a real gateway or native policy adapter, TypeScript middleware, ledger hardening, live registry connectors | P1 | 4 to 5 |

## 6. Success metrics

Setup and usability:

- Time to first governed decision: under 60 minutes from a clean checkout on `examples/mcp-demo`, following only the README. This is a starting proposal; record the actual time.
- Time to first signed policy version.
- Inspector coverage: share of the MCP servers named that produce a complete inventory entry.

Correctness:

- Scenario suite 21 of 21 in CI.
- A tampered ledger row fails `audit verify`.

Signals, not guarantees:

- External users running the middleware; third-party implementations of the adapter contract.

- Time to first graded review: from an inventory to a one-page result. Record the actual time.
- Evidence coverage: the share of review answers backed by `derived` or `observed` evidence rather than asserted.

Not a metric: GitHub stars. They measure attention, not use.

## 7. Phases

Phases are cumulative. P0 (the MVP) is phases 0 to 3. Each phase has one purpose, a list of what it delivers, and an exit that can be checked. SPEC section 14 maps each phase to the spec sections in force. Status is as of 19 October 2026.

| Phase | Purpose | Delivers | Exit |
| --- | --- | --- | --- |
| 0 | **See.** Make authority visible before anything is installed | In build order: the gap review and intake templates with their reader (built); inventory format and registry exports (specified); inventory models, agentcore and vertex readers, `ritvo inspect --registry`; owner overlay (proposed); the scoring contract with `needs_validation`, evidence coverage and answer provenance; config readers, `ritvo inspect --defaults`; the intake reader called by `ritvo diagnose`, with suggested fixes; drafted answers from an inventory; additive re-run; an interview assistant limited in code; DoA import; questionnaire init; the HTML inspect report | A first graded review from an inventory in one sitting, with no answer typed that a file could supply; an unknown is not scored as a gap; the inventory feeds a draft `ritvo.yaml`; gap review and diagnostic run on files |
| 1 | **Observe.** Put Ritvo beside a real MCP server without changing any outcome | Contract v0.2, identity, ledger, routing, mock, middleware in observe mode, demo MCP server, CLI, MCP introspection (with consent), `ritvo import`, inventory diff, `--pdf` and `--redact`; observe-mode records that show the decision enforce would have made, which supply the Observe answers in section 8.3 of the SPEC | `ritvo demo` runs; tests green; at least one answer is drafted from observe-mode records |
| 2 | **Decide.** Resolve an ask with the right people, once | Grants, ladders, separation of duties, approvals page, scenario suite, middleware self-report; the interview assistant runs as a governed agent | Suite green; tag v0.1 |
| 3 | **Govern.** Change the rules with the right sign-off, and reduce how often a person is asked | Precedent, multi-team policy governance, authority diff, metrics; observe-derived policy proposals and pre-ask resolution against standing rules (both proposed) | Ask end to end with an approval and cool-off; a proposal drafted from observed traffic is disposed of by a person in a batch; no machine draft approves |
| 4 | Harden and widen | Ledger hardening, read-only views, TypeScript middleware, first real adapter | Adapter passes the suite |
| 5 | Govern in a UI | Governance UI | Authority matrix and change requests usable |

Status of phase 0:

| State | Items |
| --- | --- |
| Built | `ritvo review`; intake templates and their reader (the reader is not yet called by `ritvo diagnose`); repository hygiene and CI |
| Specified, not built | Inventory format; registry exports; scoring contract; drafted answers; assisted drafting rules; config discovery locations; owner overlay |
| Proposed, not agreed | Help text and remedy hints for export errors |
| Not started | Additive re-run; interview assistant; DoA import; questionnaire init; `ritvo diagnose` beyond templates |

Where a person is still asked, by phase:

| Phase | What machines do | What a person still does |
| --- | --- | --- |
| 0 | Discover agents and tools from files and exports; draft the answers for the 7 Derive questions; score with evidence coverage | Confirm the draft; assign owners where a platform supplies none; answer the 5 questions only a person can answer |
| 1 | Add the 5 Observe answers from observe-mode records; show what enforce would have decided | Confirm those answers; decide when to leave observe mode |
| 2 | Route asks, hold and release, mint single-use grants | Approve or reject each ask, as a named approver, not their own agent's |
| 3 | Draft policy proposals from observed traffic; resolve asks that standing rules already cover | Dispose of proposals in batches; sign off policy changes; own each standing rule |

The 7, 5 and 5 split is a first reading of the question text (SPEC 8.3) and is to be checked when it is built.

## 8. Risks and assumptions

| Risk | Mitigation |
| --- | --- |
| A platform or gateway ships ladders and separation of duties by default | Stay below the gateway and neutral; make the adapter contract and suite the credibility asset |
| The user is absent at the MCP server in unattended mode | `mode` and `owner` in the principal; unattended requires a registered owner |
| Grants are untested | Phase 2 suite scenarios 17 to 21; hold-and-release remains in the mock |
| Gateway-side approvals make a request ask twice | Ritvo's decision is authoritative; gateway approvals are recorded as signals |
| Token claims differ by identity provider | Per-provider claims-mapping file; part of the setup-time metric |
| Inspecting a stdio MCP server runs code from a config file | No spawning without explicit per-server consent |
| No proven buyer | Commercial gates stay separate; the repo does not depend on them |
| Maintainer load once there are outside users | Decide a support promise before announcing |

Assumption to test: default-deny is blocking production agents where this would be used.

## 9. Open questions

- Can Cedar express a delegation chain, or does it need request context plus a custom check?
- Which claims does each identity provider put in an exchanged token?
- Which unattended-mode setups leave an MCP server with no user, and how common is that?
- Will any platform gateway accept an external approval hook or a signed grant?
- How should a grant ride on an MCP request: header or request metadata?
- What support promise does the public repo make?

## 10. Revision history

Append-only. A change adds a row; earlier rows are not edited.

| Version | Date | Change |
| --- | --- | --- |
| draft v0.1 | 13 October 2026 | First draft |
| draft v0.2 | 19 October 2026 | Phases 0 to 3 defined with purpose, deliverables, exit and status (section 7). Requirements added for the scoring split, drafted answers, additive re-run, interview assistant, owner overlay, machine-draft rule, observe-derived proposals and pre-ask resolution. Gap review question count corrected to 17. Metrics added. |
| draft v0.3 | 19 October 2026 | Owner overlay specified (R-INS-9, SPEC 7.4) and moved to "Specified, not built" in the phase 0 status (section 7). |
