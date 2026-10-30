"""The authority matrix, built from a diagnosis (docs/SPEC.md section 8.5)."""

import csv
import io

from ritvo.diagnostic.gaps.model import Diagnosis, Finding, ladder_roles
from ritvo.diagnostic.gaps.rules import RULES

COLUMNS = (
    "agent_id",
    "agent_name",
    "owner",
    "owner_team",
    "mode",
    "tool_id",
    "system",
    "access",
    "tier",
    "tool_owner_team",
    "proposed_decision",
    "approval_ladder",
    "ladder_holders",
    "gaps",
)
# A spreadsheet reads a cell that starts with one of these as a formula.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def cell(value: str | None) -> str:
    """A cell for a spreadsheet: empty when not known, and never read as a formula."""
    if value is None:
        return ""
    return "'" + value if value.startswith(_FORMULA) else value


def _gaps(findings: list[Finding], agent: str, tool: str) -> str:
    """The rules whose findings name this agent and tool; one not known ends in :unknown."""
    marks = {
        (f.rule if f.level == "gap" else f"{f.rule}:unknown")
        for f in findings
        if agent in f.agents and (not f.tools or tool in f.tools)
    }
    return ";".join(sorted(marks, key=lambda mark: (RULES.index(mark.split(":")[0]), mark)))


def matrix_text(diagnosis: Diagnosis) -> str:
    agents = {agent.id: agent for agent in diagnosis.agents or ()}
    tools = {tool.id: tool for tool in diagnosis.tools or ()}
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for binding in diagnosis.bindings:
        agent = agents[binding.agent]
        tool = tools.get(binding.tool)
        ladder = ladder_roles(tool.approvals) if tool is not None else []
        holders = sorted({person for role in ladder for person in diagnosis.roles.get(role, [])})
        writer.writerow(
            [
                cell(value)
                for value in (
                    agent.id,
                    agent.name,
                    agent.owner,
                    agent.owner_team,
                    agent.mode,
                    binding.tool,
                    tool.system if tool else None,
                    tool.access if tool else None,
                    binding.tier,
                    binding.owner_team,
                    binding.decision,
                    ";".join(ladder),
                    ";".join(holders),
                    _gaps(diagnosis.findings, agent.id, binding.tool),
                )
            ]
        )
    return out.getvalue()
