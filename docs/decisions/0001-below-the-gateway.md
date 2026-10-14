# 0001. Ritvo sits below the gateway and is not one

Status: accepted · Date: 2026-10-14

## Context

Agents reach tools and data through gateways: commercial, cloud-provider and open-source. Gateways already sell enforcement: key issuance, budgets, provider proxying, tool allow lists, rate limits and audit logs. Every major cloud and identity vendor ships some form of it, and so do several gateway products. Enforcement is becoming a commodity.

What is missing is the organisation's authority model at runtime: who may write the policy, who decides when an agent asks for something outside it, who that decision escalates to, whether the requester or the agent's owner can also approve, and what record shows who authorised what under which policy version (see `docs/PRD.md`, section 2).

There were three broad options:

1. Build a gateway that also does authority.
2. Build authority as a plug-in for one or more existing gateways.
3. Put enforcement next to the resource, below any gateway, and keep the authority decisions in a separate service that never touches a live call.

## Decision

Ritvo takes option 3.

- **Enforcement points run next to the resource.** The first is middleware inside the client's own MCP server. Adopting it means one library call, reversible by removing it. It works behind any gateway, or none.
- **The Governance OS is the policy authority.** It publishes signed policy bundles to enforcement points, resolves asks along escalation ladders, enforces separation of duties, mints signed single-use grants and keeps the precedent log. It never touches, holds or executes a live call.
- **Ritvo does not rebuild what gateways sell:** no key issuance, budgets, provider proxying or tool allow lists, no credential custody, no TLS interception.
- **A gateway-side approval is an upstream signal.** It is recorded in the ledger and joined by `event_id` and fingerprint, but it never replaces Ritvo's decision on who may decide.
- **Gateways attach through an open adapter contract** (`docs/SPEC.md`, section 6.1). An adapter is called supported only when it passes the scenario suite (section 10), and only after the suite passes against the mock control plane and the middleware.

## Consequences

Positive:

- Ritvo is neutral across gateways, agent platforms and identity providers, and does not compete with the products it has to work beside.
- The resource owner can adopt it without changing their gateway, and remove it the same way.
- Decisions are made where the resource's owner can see them, with the principal chain validated at the resource's own boundary.
- Being off the request path keeps the Governance OS simple: an outage there degrades per tier (high tier fails closed) rather than taking traffic down.

Negative or open:

- Coverage depends on enforcement points being installed next to each resource; a resource without one is not governed. The inspector exists to make those gaps visible.
- An agent that is refused may try another route to the same resource. Handling that is the least proven part of the design, and the scenario suite exists to test it.
- Enforcement points must evaluate policy locally and accept signed grants, so bundle distribution, key handling and fail modes have to be right (`docs/SPEC.md`, sections 4.5, 4.6 and 5.6).
- A gateway that ships ladders and separation of duties by default would narrow the gap Ritvo fills. The mitigation is to stay neutral and make the adapter contract and scenario suite the credibility asset.
