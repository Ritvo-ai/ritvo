"""Render an inventory as one self-contained HTML report (docs/SPEC.md section 7.2)."""

import hashlib
import re
from pathlib import Path
from typing import Any

import jinja2

from ritvo import __version__
from ritvo.inspector.inventory import Agent, Finding, Inventory, McpServer, Tool
from ritvo.inspector.registry import EXPORTS

_ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("ritvo.inspector", "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

TIERS = ("low", "medium", "high")
_RANK = {"low": 1, "medium": 2, "high": 3}
_CREDENTIAL = re.compile(r"(_API_KEY|_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$")
_P1_RULES = ("tool.destructive_unowned", "mcp.no_auth")
_RULE_ORDER = (
    "tool.destructive_unowned",
    "mcp.no_auth",
    "agent.no_owner",
    "identity.shared_key_name",
)

_WHY = {
    "agent.no_owner": "Approvals and escalations need a named person to start from.",
    "identity.shared_key_name": (
        "A shared credential name suggests two agents may be indistinguishable to the services "
        "they call."
    ),
    "tool.destructive_unowned": (
        "A tool that can change or delete things at the high tier can be reached with nobody "
        "accountable."
    ),
    "mcp.no_auth": (
        "This scan cannot show who a caller is at this server. It may still be protected "
        "another way, for example by a network rule."
    ),
}
_NEXT = {
    "agent.no_owner": "Name an owner and owner team for the agent.",
    "identity.shared_key_name": "Check whether each agent has its own credential.",
    "tool.destructive_unowned": (
        "Assign owners to the agents involved, then decide whether the tool asks on every call."
    ),
    "mcp.no_auth": (
        "Confirm how the server authenticates callers. If it does not, add token validation "
        "before turning enforcement on."
    ),
}


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def tier_of(tool: Tool) -> str:
    return tool.proposed_tier or "unknown"


def _inspected(server: McpServer, inventory: Inventory) -> bool:
    return server.id in inventory.manifest.servers_contacted


def _reach(inventory: Inventory) -> dict[str, list[Tool]]:
    """The tools each agent is known to reach. Only tools found in code record their reach."""
    reach: dict[str, list[Tool]] = {agent.id: [] for agent in inventory.agents}
    for tool in inventory.tools:
        for agent_id in tool.reached_by:
            reach.setdefault(agent_id, []).append(tool)
    return reach


def _high(tools: list[Tool]) -> int:
    return sum(1 for tool in tools if tool.proposed_tier == "high")


def _mix(tools: list[Tool]) -> dict[str, int]:
    mix = {tier: 0 for tier in (*TIERS, "unknown")}
    for tool in tools:
        mix[tier_of(tool)] += 1
    return mix


def _authorization(server: McpServer) -> tuple[str, str]:
    if server.transport == "stdio":
        return "local", "Local process"
    if server.auth is None:
        return "unknown", "Unknown: not inspected"
    if not server.auth.protected_resource_metadata:
        return "none", "None found"
    issuers = ", ".join(server.auth.authorization_servers)
    return "advertised", f"Advertised: {issuers}" if issuers else "Advertised"


def _server_rows(inventory: Inventory) -> list[dict[str, Any]]:
    rows = []
    for server in inventory.mcp_servers:
        tools = [tool for tool in inventory.tools if tool.server == server.id]
        inspected = _inspected(server, inventory)
        kind, text = _authorization(server)
        if not inspected:
            order = "Inspect first"
        elif kind == "none":
            order = "Confirm authorization first"
        elif _high(tools):
            order = "Wrap early"
        else:
            order = "Wrap later"
        rows.append(
            {
                "id": server.id,
                "transport": server.transport,
                "target": server.url or server.command or "",
                "auth_kind": kind,
                "auth": text,
                "inspected": inspected,
                "tools": len(tools) if inspected else None,
                "mix": _mix(tools),
                "scopes": server.auth.scopes_supported if server.auth else None,
                "order": order,
            }
        )
    return rows


def _tool_rows(inventory: Inventory) -> list[dict[str, Any]]:
    rows = []
    for tool in inventory.tools:
        flags = [
            (name, value)
            for name, value in (
                ("read-only", tool.annotations.readOnly),
                ("destructive", tool.annotations.destructive),
                ("idempotent", tool.annotations.idempotent),
                ("open-world", tool.annotations.openWorld),
            )
        ]
        rows.append(
            {
                "origin": tool.server or "code",
                "name": tool.id.split("/", 1)[1] if tool.server else tool.id,
                "source": tool.source,
                "flags": [(name, value) for name, value in flags],
                "tier": tier_of(tool),
            }
        )
    return rows


def _priority(finding: Finding, reach: dict[str, list[Tool]]) -> int:
    if finding.rule in _P1_RULES:
        return 1
    if finding.rule == "agent.no_owner" and _high(reach.get(finding.subject, [])):
        return 1
    return 2


def _saw(finding: Finding, reach: dict[str, list[Tool]]) -> str:
    agents = ", ".join(finding.agents)
    if finding.rule == "agent.no_owner":
        tools = reach.get(finding.subject, [])
        if not tools:
            return "No owner in the inventory. No tool reach is known."
        return (
            f"No owner in the inventory. Reaches {plural(len(tools), 'tool')} found in code, "
            f"{_high(tools)} at the high tier."
        )
    if finding.rule == "identity.shared_key_name":
        return (
            f"The credential name {finding.subject} is used by {agents}. "
            "Names only; values were never read."
        )
    if finding.rule == "tool.destructive_unowned":
        if finding.agents:
            return f"Proposed at the high tier. Reached by {agents}, which have no owner."
        return "Proposed at the high tier. No known agent reaches it."
    return "HTTP server with no authorization advertised."


def _findings(inventory: Inventory, reach: dict[str, list[Tool]]) -> list[dict[str, Any]]:
    rows = [
        {
            "priority": _priority(finding, reach),
            "rule": finding.rule,
            "subject": finding.subject,
            "saw": _saw(finding, reach),
            "why": _WHY[finding.rule],
            "next": _NEXT[finding.rule],
        }
        for finding in inventory.findings
    ]
    return sorted(
        rows, key=lambda row: (row["priority"], _RULE_ORDER.index(row["rule"]), row["subject"])
    )


def _agent_rows(agents: list[Agent], reach: dict[str, list[Tool]]) -> list[dict[str, Any]]:
    rows = []
    for agent in agents:
        tools = reach[agent.id]
        ranks = [_RANK[tool.proposed_tier] for tool in tools if tool.proposed_tier]
        highest = {rank: tier for tier, rank in _RANK.items()}[max(ranks)] if ranks else "unknown"
        rows.append(
            {
                "id": agent.id,
                "owner": agent.owner,
                "team": agent.owner_team,
                "framework": agent.framework,
                "high": _high(tools),
                "highest": highest,
                "unknown_tiers": any(tool.proposed_tier is None for tool in tools),
            }
        )
    return rows


def _matrix(inventory: Inventory, reach: dict[str, list[Tool]]) -> dict[str, Any]:
    columns = [tool for tool in inventory.tools if tool.reached_by]
    rows = [
        {
            "agent": agent.id,
            "cells": [tier_of(tool) if agent.id in tool.reached_by else None for tool in columns],
        }
        for agent in inventory.agents
    ]
    return {"columns": [tool.id for tool in columns], "rows": rows}


def _decisions(inventory: Inventory, servers: list[dict[str, Any]]) -> list[dict[str, str]]:
    decisions = []
    unowned = [agent.id for agent in inventory.agents if agent.owner is None]
    if unowned:
        decisions.append(
            {
                "text": f"Name an owner and team for {', '.join(unowned)}.",
                "who": "Agent sponsor",
            }
        )
    high = sum(1 for tool in inventory.tools if tool.proposed_tier == "high")
    if high:
        decisions.append(
            {
                "text": f"Confirm the {plural(high, 'high-tier tool')} should ask for approval "
                "on every call.",
                "who": "Policy owner",
            }
        )
    uninspected = [row["id"] for row in servers if not row["inspected"]]
    if uninspected:
        decisions.append(
            {
                "text": f"Decide whether to inspect {', '.join(uninspected)} so its tools can "
                "be listed.",
                "who": "Platform lead",
            }
        )
    return decisions


def _controls(inventory: Inventory, servers: list[dict[str, Any]]) -> list[dict[str, str]]:
    agents = inventory.agents
    unowned = sum(1 for agent in agents if agent.owner is None)
    if not agents:
        ownership = ("na", "No agents were found")
    elif unowned == 0:
        ownership = ("ok", f"All {plural(len(agents), 'agent')} have an owner")
    else:
        status = "gap" if unowned == len(agents) else "partial"
        ownership = (status, f"{unowned} of {len(agents)} agents have no owner")
    http = [row for row in servers if row["transport"] == "http"]
    advertised = sum(1 for row in http if row["auth_kind"] == "advertised")
    if not http:
        access = ("na", "No HTTP servers were found")
    elif any(row["auth_kind"] == "none" for row in http):
        access = ("gap", f"{advertised} of {len(http)} HTTP servers advertise authorization")
    elif any(row["auth_kind"] == "unknown" for row in http):
        access = (
            "partial",
            f"{advertised} of {len(http)} HTTP servers advertise authorization; "
            "the rest were not inspected",
        )
    else:
        access = ("ok", f"All {plural(len(http), 'HTTP server')} advertise authorization")
    uninspected = sum(1 for row in servers if not row["inspected"])
    unreadable = len(inventory.manifest.unreadable)
    if not agents and not servers:
        listed = ("na", "Nothing was found")
    elif uninspected or unreadable:
        listed = (
            "partial",
            f"{plural(uninspected, 'server')} not inspected, {plural(unreadable, 'file')} "
            "unreadable",
        )
    else:
        listed = ("ok", "Every server found was inspected and every file read")
    return [
        {"area": "Inventory of agents and tools", "evidence": listed[1], "status": listed[0]},
        {
            "area": "Named accountable owner for each agent",
            "evidence": ownership[1],
            "status": ownership[0],
        },
        {"area": "Access control on tool servers", "evidence": access[1], "status": access[0]},
        {
            "area": "Human oversight assigned to named people",
            "evidence": "Approver roles are set in Ritvo, not found by a scan",
            "status": "na",
        },
        {
            "area": "Logging of agent actions",
            "evidence": "This scan reads configuration. It does not read runtime logs",
            "status": "na",
        },
    ]


def _credentials(inventory: Inventory) -> list[dict[str, Any]]:
    names = {name for name in inventory.manifest.env_names_seen if _CREDENTIAL.search(name)}
    names |= {agent.credential for agent in inventory.agents if agent.credential}
    return [
        {
            "name": name,
            "agents": sorted(agent.id for agent in inventory.agents if agent.credential == name),
        }
        for name in sorted(names)
    ]


def build(inventory: Inventory, sha256: str) -> dict[str, Any]:
    reach = _reach(inventory)
    servers = _server_rows(inventory)
    findings = _findings(inventory, reach)
    agents = inventory.agents
    unowned = sum(1 for agent in agents if agent.owner is None)
    http = [row for row in servers if row["transport"] == "http"]
    uninspected = sum(1 for row in servers if not row["inspected"])
    high = sum(1 for tool in inventory.tools if tool.proposed_tier == "high")
    unknown_tier = sum(1 for tool in inventory.tools if tool.proposed_tier is None)
    p1 = sum(1 for finding in findings if finding["priority"] == 1)
    sentences = [
        f"Found {plural(len(agents), 'agent')}, {plural(len(servers), 'MCP server')} and "
        f"{plural(len(inventory.tools), 'tool')}."
    ]
    if unowned:
        sentences.append(f"{unowned} of {len(agents)} agents have no owner.")
    if uninspected:
        sentences.append(
            f"{plural(uninspected, 'server')} not inspected, so its tools are unknown."
        )
    return {
        "version": __version__,
        "inventory": inventory,
        "sha256": sha256,
        "lede": " ".join(sentences),
        "readings": [
            {
                "label": "Agents",
                "value": len(agents),
                "note": f"{len(agents) - unowned} with a named owner",
            },
            {
                "label": "MCP servers",
                "value": len(servers),
                "note": f"{len(servers) - uninspected} inspected, "
                f"{sum(1 for row in http if row['auth_kind'] == 'advertised')} of {len(http)} "
                "HTTP servers advertise authorization",
            },
            {
                "label": "Tools",
                "value": len(inventory.tools),
                "note": f"{high} would start at the high tier, {unknown_tier} tier unknown",
            },
            {
                "label": "Findings",
                "value": len(findings),
                "note": f"{p1} to fix first",
            },
        ],
        "servers": servers,
        "tools": _tool_rows(inventory),
        "findings": findings,
        "p1": p1,
        "stdio": [row for row in servers if row["transport"] == "stdio"],
        "http": http,
        "credentials": _credentials(inventory),
        "decisions": _decisions(inventory, servers),
        "agents": _agent_rows(agents, reach),
        "matrix": _matrix(inventory, reach),
        "controls": _controls(inventory, servers),
        "exports": [
            {"platform": platform, "commands": commands}
            for platform, (_, commands) in EXPORTS.items()
            if platform not in {export.platform for export in inventory.manifest.registry_exports}
        ],
        "tiers": TIERS,
        "uninspected": uninspected,
    }


def render_html(inventory: Inventory, sha256: str) -> str:
    return _ENV.get_template("report.html.j2").render(**build(inventory, sha256))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_report(inventory: Inventory, sha256: str, out: Path) -> None:
    out.write_text(render_html(inventory, sha256), encoding="utf-8", newline="\n")
