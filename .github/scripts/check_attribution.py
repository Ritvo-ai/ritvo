"""Commit hygiene check: no tool or assistant attribution, and a DCO sign-off on every commit.

Usage:
  check_attribution.py --message-file PATH   commit-msg hook: patterns and sign-off
  check_attribution.py --env NAME [NAME...]  PR title and body from env variables: patterns
  check_attribution.py --commits REV_RANGE   every commit in a revision range: patterns and sign-off

Exits 1 and lists every problem found, or exits 0.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Names that are also common first names (for example Devin) are left out so that human
# co-authors are not blocked; bot accounts are caught by "[bot]".
ASSISTANT_NAMES = (
    r"claude|anthropic|copilot|chatgpt|openai|gpt[\w.-]*|gemini|bard|cursor|codeium|windsurf"
    r"|aider|codex|tabnine|codewhisperer|amazon q|ai assistant"
)

FORBIDDEN = (
    (
        "co-authored-by trailer naming a tool or assistant",
        re.compile(
            rf"^\s*co-authored-by:.*(\b({ASSISTANT_NAMES})\b|\[bot\])",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    ("'generated with' line", re.compile(r"generated with", re.IGNORECASE)),
    (
        "session or chat URL",
        re.compile(
            r"https?://(www\.)?(claude\.ai|chatgpt\.com|chat\.openai\.com|gemini\.google\.com"
            r"|aistudio\.google\.com|copilot\.microsoft\.com|perplexity\.ai|poe\.com"
            r"|chat\.mistral\.ai)\b",
            re.IGNORECASE,
        ),
    ),
    ("assistant noreply address", re.compile(r"noreply@anthropic\.com", re.IGNORECASE)),
    ("robot emoji", re.compile("\N{ROBOT FACE}")),
)

SIGNED_OFF_BY = re.compile(r"^Signed-off-by: .+ <[^<>@\s]+@[^<>@\s]+>\s*$", re.MULTILINE)

SCISSORS = "# ------------------------ >8 ------------------------"

# Dependabot cannot add a DCO sign-off; its commits are still checked for attribution.
SIGNOFF_EXEMPT_AUTHORS = ("49699333+dependabot[bot]@users.noreply.github.com",)


def find_attribution(text: str) -> list[str]:
    """Return a description of each forbidden pattern found in text."""
    return [label for label, pattern in FORBIDDEN if pattern.search(text)]


def has_signoff(message: str) -> bool:
    return SIGNED_OFF_BY.search(message) is not None


def clean_commit_message(raw: str) -> str:
    """Drop git comment lines and anything below the scissors line, as git does."""
    kept = []
    for line in raw.splitlines():
        if line.startswith(SCISSORS):
            break
        if not line.startswith("#"):
            kept.append(line)
    return "\n".join(kept)


def check_message(message: str, *, require_signoff: bool) -> list[str]:
    problems = find_attribution(message)
    if require_signoff and not has_signoff(message):
        problems.append("missing Signed-off-by line (use git commit -s)")
    return problems


def check_commits(rev_range: str) -> list[str]:
    git = shutil.which("git")
    if git is None:
        return ["git not found"]
    output = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [git, "log", "--format=%H%x1f%ae%x1f%B%x1e", rev_range],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    problems = []
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, author_email, message = record.split("\x1f", 2)
        exempt = author_email in SIGNOFF_EXEMPT_AUTHORS
        for problem in check_message(message, require_signoff=not exempt):
            problems.append(f"{sha[:12]}: {problem}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message-file", type=Path)
    group.add_argument("--env", nargs="+", metavar="NAME")
    group.add_argument("--commits", metavar="REV_RANGE")
    args = parser.parse_args(argv)

    if args.message_file is not None:
        message = clean_commit_message(args.message_file.read_text(encoding="utf-8"))
        problems = check_message(message, require_signoff=True)
    elif args.env is not None:
        problems = [
            f"{name}: {problem}"
            for name in args.env
            for problem in find_attribution(os.environ.get(name, ""))
        ]
    else:
        problems = check_commits(args.commits)

    for problem in problems:
        print(f"commit hygiene: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
