# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it through GitHub private vulnerability reporting: go to the [Security tab](https://github.com/Ritvo-ai/ritvo/security) of this repository and choose **Report a vulnerability**. If you cannot use GitHub, email security@ritvo.ai.

Include what you found, how to reproduce it, the version or commit you tested, and the impact you expect. Do not include real credentials, tokens or client data in a report; use fictional values.

## Response

We acknowledge reports within 7 days and aim to share a fix or mitigation plan within 30 days.

## Supported versions

Only the latest release, and `main` before the first release, receive security fixes.

| Version | Supported |
| --- | --- |
| latest release | yes |
| older releases | no |

## Scope

In scope: code in this repository, including the Governance OS, the enforcement middleware, the inspector, the diagnostic toolkit, the ledger and the CLI.

Out of scope: third-party gateways, identity providers and MCP servers that Ritvo works with, and the mock control plane (`mock_control_plane/`), which is a test harness and is not meant to be deployed.

Prompt injection against an agent is a known limit of any authority layer. Ritvo's defence is structural (parameter limits and a mandatory ask on the high tier). A report showing that one of those structural controls can be bypassed is in scope.
