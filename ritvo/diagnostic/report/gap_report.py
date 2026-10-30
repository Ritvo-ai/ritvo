"""Render a diagnosis as one self-contained HTML page (docs/SPEC.md section 8.5)."""

from typing import Any

import jinja2

from ritvo import __version__
from ritvo.diagnostic.gaps.model import Diagnosis
from ritvo.diagnostic.gaps.rules import RULES, TITLES

_ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("ritvo.diagnostic.report", "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _counts(diagnosis: Diagnosis) -> list[dict[str, Any]]:
    return [
        {
            "rule": rule,
            "title": TITLES[rule],
            "gaps": sum(1 for f in diagnosis.findings if f.rule == rule and f.level == "gap"),
            "unknown": sum(
                1 for f in diagnosis.findings if f.rule == rule and f.level == "needs_validation"
            ),
        }
        for rule in RULES
    ]


def _grid(diagnosis: Diagnosis) -> dict[str, Any]:
    described = {tool.id: tool.description for tool in diagnosis.tools or ()}
    tools = list(described)
    for binding in diagnosis.bindings:
        if binding.tool not in tools:
            tools.append(binding.tool)
    decisions = {(b.agent, b.tool): b for b in diagnosis.bindings}
    return {
        "tools": [{"id": tool, "description": described.get(tool)} for tool in tools],
        "rows": [
            {
                "agent": agent.id,
                "name": agent.name,
                "cells": [decisions.get((agent.id, tool)) for tool in tools],
            }
            for agent in diagnosis.agents or ()
        ],
    }


def build(diagnosis: Diagnosis, diagnosis_sha256: str) -> dict[str, Any]:
    gaps = sum(1 for f in diagnosis.findings if f.level == "gap")
    unknown = sum(1 for f in diagnosis.findings if f.level == "needs_validation")
    findings = sorted(
        diagnosis.findings,
        key=lambda f: (f.level != "gap", RULES.index(f.rule), f.subject),
    )
    return {
        "version": __version__,
        "diagnosis": diagnosis,
        "diagnosis_sha256": diagnosis_sha256,
        "gaps": gaps,
        "unknown": unknown,
        "counts": _counts(diagnosis),
        "findings": findings,
        "grid": _grid(diagnosis),
        "titles": TITLES,
        "agents": len(diagnosis.agents or ()),
        "tools": len(diagnosis.tools or ()),
        "people": len(diagnosis.people or ()),
    }


def render_html(diagnosis: Diagnosis, diagnosis_sha256: str) -> str:
    return _ENV.get_template("gap-report.html.j2").render(**build(diagnosis, diagnosis_sha256))
