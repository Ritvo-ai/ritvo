"""The canary self-test of the Cedar engine (docs/SPEC.md section 5.9, control 2).

A fixed set of requests with known answers. It is written by hand, not compiled, so it does not
depend on the code it checks.
"""

from typing import Any

POLICIES = {
    "deny": (
        '@id("c-deny")\npermit(principal == Agent::"a1", action == Action::"call", '
        'resource == Tool::"t.deny");\n'
    ),
    "over_limit": (
        '@id("c-limit")\npermit(principal == Agent::"a1", action == Action::"call", '
        'resource == Tool::"t.low") unless { (context.params has v && context.params.v <= 5) };\n'
    ),
    "ask": (
        '@id("c-high")\npermit(principal == Agent::"a1", action == Action::"call", '
        'resource == Tool::"t.high");\n'
        '@id("c-team")\npermit(principal in Team::"x", action == Action::"call", '
        'resource == Tool::"t.team");\n'
    ),
    "allow": (
        '@id("c-low")\npermit(principal == Agent::"a1", action == Action::"call", '
        'resource == Tool::"t.low") when { (context.params has v && context.params.v <= 5) };\n'
    ),
}

ENTITIES: list[dict[str, Any]] = [
    {"uid": {"type": "Agent", "id": "a1"}, "attrs": {}, "parents": [{"type": "Team", "id": "x"}]},
    {"uid": {"type": "Agent", "id": "a2"}, "attrs": {}, "parents": []},
    {"uid": {"type": "Team", "id": "x"}, "attrs": {}, "parents": []},
    {"uid": {"type": "Tool", "id": "t.low"}, "attrs": {"tier": "low"}, "parents": []},
    {"uid": {"type": "Tool", "id": "t.high"}, "attrs": {"tier": "high"}, "parents": []},
    {"uid": {"type": "Tool", "id": "t.team"}, "attrs": {"tier": "medium"}, "parents": []},
    {"uid": {"type": "Tool", "id": "t.deny"}, "attrs": {"tier": "low"}, "parents": []},
]

# (agent, tool, params, expected outcome, expected policy id)
CASES: list[tuple[str, str, dict[str, int], str, str | None]] = [
    ("a1", "t.low", {"v": 1}, "allow", "c-low"),
    ("a1", "t.low", {"v": 9}, "over_limit", "c-limit"),
    ("a1", "t.low", {}, "over_limit", "c-limit"),
    ("a1", "t.high", {}, "ask", "c-high"),
    ("a1", "t.team", {}, "ask", "c-team"),
    ("a1", "t.deny", {}, "deny", "c-deny"),
    ("a1", "t.unknown", {}, "none", None),
    ("a2", "t.high", {}, "none", None),
    ("a2", "t.team", {}, "none", None),
]
