"""The draft ritvo.yaml, built from a diagnosis (docs/SPEC.md section 8.5)."""

import yaml

from ritvo.diagnostic.gaps.model import Diagnosis, Tool


def _tool(tool: Tool) -> dict[str, object]:
    entry: dict[str, object] = {"id": tool.id, "tier": tool.tier}
    if tool.approval_ttl_minutes is not None:
        entry["approval_ttl_minutes"] = tool.approval_ttl_minutes
    if tool.approvals is not None:
        entry["approvals"] = {
            "parameter": tool.approvals.parameter,
            "currency": tool.approvals.currency,
            "bands": [
                {**({} if band.up_to is None else {"up_to": band.up_to}), "ladder": band.ladder}
                for band in tool.approvals.bands
            ],
        }
    return entry


def draft_data(diagnosis: Diagnosis) -> dict[str, object]:
    data: dict[str, object] = {"mode": "observe", "profile": "local"}
    if diagnosis.agents is not None:
        data["agents"] = [
            {
                "id": agent.id,
                "owner": agent.owner,
                "owner_team": agent.owner_team,
                "mode": agent.mode,
            }
            for agent in diagnosis.agents
        ]
    if diagnosis.tools is not None:
        data["tools"] = [_tool(tool) for tool in diagnosis.tools]
    if diagnosis.agents is not None:
        data["bindings"] = [
            {
                "agent": binding.agent,
                "tool": binding.tool,
                "decision": binding.decision,
                "owner_team": binding.owner_team,
            }
            for binding in diagnosis.bindings
        ]
    if diagnosis.people is not None or diagnosis.doa_given:
        data["roles"] = diagnosis.roles
    return data


def draft_text(diagnosis: Diagnosis) -> str:
    return yaml.safe_dump(draft_data(diagnosis), sort_keys=False, allow_unicode=True)
