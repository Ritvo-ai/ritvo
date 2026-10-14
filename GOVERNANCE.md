# Governance

## Maintainers

Ritvo has a single maintainer for now:

| Name | GitHub |
| --- | --- |
| Sunil Kalkunte | [@sunilkalkunte](https://github.com/sunilkalkunte) |

## How decisions are made

- Day-to-day decisions (reviews, merges, issue triage) are made by the maintainer.
- Changes to behaviour or interfaces start as an issue or pull request that changes [docs/SPEC.md](docs/SPEC.md). The spec is the source of truth; code follows it.
- Significant design decisions are recorded as numbered decision records in [docs/decisions/](docs/decisions/), with the context, the decision and its consequences.
- Discussion happens in public issues and pull requests, so the reasoning stays visible. Security matters are the exception and follow [SECURITY.md](SECURITY.md).

## Becoming a maintainer

A contributor can become a maintainer after a sustained record of good-quality contributions and reviews, an understanding of the spec and the project's non-goals, and adherence to the [Code of Conduct](CODE_OF_CONDUCT.md). The existing maintainers invite new maintainers; once there is more than one, an invitation needs the agreement of a majority of them. This file is updated in the same pull request.

A maintainer may step down at any time. A maintainer who has been inactive for twelve months may be moved to emeritus status by the other maintainers.

## Neutrality

Ritvo stays neutral across agent platforms, identity providers and gateways.

- The adapter contract is open and versioned. Anyone may implement it, and an adapter is called supported only when it passes the scenario suite, whoever wrote it.
- No vendor can buy features, priority, roadmap position or a "supported" label. Sponsorship or employment does not change how a contribution is reviewed.
- Maintainers declare any relevant affiliation when it bears on a decision, and step back from decisions where they have a conflict of interest.

## Transferring maintainership

If the maintainer can no longer continue, maintainership transfers to another maintainer if there is one. Otherwise the maintainer may name a successor from among active contributors in a public issue, with a notice period of at least 30 days for objections. The repository, package names and the project's trademark position (see [TRADEMARK.md](TRADEMARK.md)) transfer with maintainership. If no successor is found, the repository is archived with a notice in this file and the README.

## Changing this document

Changes to this file go through a pull request and are announced in the changelog.
