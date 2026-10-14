"""The gap review's areas, questions and option levels (docs/SPEC.md section 8.1)."""

from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    GAP = "gap"
    PARTIAL = "partial"
    IN_PLACE = "in_place"
    NOT_ANSWERED = "not_answered"


LEVEL_LABELS = {
    Level.GAP: "Gap",
    Level.PARTIAL: "Partial",
    Level.IN_PLACE: "In place",
    Level.NOT_ANSWERED: "Not answered",
}

# Lower ranks are weaker; an area takes the weakest answered question.
LEVEL_RANK = {Level.GAP: 0, Level.PARTIAL: 1, Level.IN_PLACE: 2}

UNKNOWN = "unknown"


@dataclass(frozen=True)
class Area:
    id: str
    title: str


@dataclass(frozen=True)
class Question:
    id: str
    area: Area
    text: str
    options: dict[str, Level]
    why: str
    next_step: str


AREAS = (
    Area("agents", "Agents and what they can do"),
    Area("approval", "Who may approve what"),
    Area("escalation", "What happens when nobody answers"),
    Area("policy_change", "How the rules change"),
    Area("record", "What record exists"),
)

_AREA = {area.id: area for area in AREAS}


def _question(
    qid: str,
    text: str,
    options: tuple[str, str, str],
    why: str,
    next_step: str,
) -> Question:
    gap, partial, in_place = options
    return Question(
        id=qid,
        area=_AREA[qid.split(".", 1)[0]],
        text=text,
        options={
            gap: Level.GAP,
            partial: Level.PARTIAL,
            in_place: Level.IN_PLACE,
            UNKNOWN: Level.GAP,
        },
        why=why,
        next_step=next_step,
    )


QUESTIONS = (
    _question(
        "agents.inventory",
        "Is there a list of every AI agent in use, including those built outside the central "
        "IT or platform team?",
        ("none", "partial", "complete"),
        "An agent that is not on a list cannot be given an owner, reviewed or switched off. "
        "Unlisted agents are where authority goes unchecked first.",
        "List every agent with the team that built it, the platform it runs on and the "
        "systems it connects to. Include agents built inside business teams.",
    ),
    _question(
        "agents.owner",
        "Does every agent have a named person who is accountable for it?",
        ("none", "some", "all"),
        "Without a named owner, nobody answers for what an agent does, and an agent acting "
        "on its own has no accountable human at all.",
        "Name one accountable person for each agent and record it with the agent. Do not "
        "use a shared mailbox or a team name alone.",
    ),
    _question(
        "agents.reach",
        "For each agent, is it written down which tools and data it may use, and which of "
        "its actions are high risk?",
        ("none", "some", "all"),
        "If reach is not written down, nobody can tell whether a request is inside an "
        "agent's normal permissions or needs a decision.",
        "For each agent, list the tools and data it may use and mark each action low, "
        "medium or high risk.",
    ),
    _question(
        "approval.named",
        "When an agent needs approval for something outside its normal permissions, who decides?",
        ("no_one", "informal", "named_role"),
        "If nobody in particular decides, requests are approved by whoever is nearest, "
        "often the person who built the agent.",
        "Name a role, not just a person, that decides for each kind of action and each "
        "risk tier, with a deputy.",
    ),
    _question(
        "approval.self",
        "Can an agent's owner, or the person the agent acts for, approve that agent's own request?",
        ("yes", "not_prevented", "prevented"),
        "When the requester or the agent's owner can approve, there is no second person "
        "and the approval adds no control.",
        "Prevent the agent's owner and the person it acts for from approving its requests "
        "above the low risk tier, and enforce it in the approval tool.",
    ),
    _question(
        "approval.context",
        "When someone approves, do they see exactly what they are approving: the agent, "
        "the person it acts for, the tool and the parameters?",
        ("no", "partly", "yes"),
        "An approver who sees only a summary can approve something different from what runs.",
        "Show the approver the agent, the person it acts for, the tool and the exact "
        "parameters, and tie the approval to that one request.",
    ),
    _question(
        "escalation.path",
        "If the approver does not respond, does the request move to someone else?",
        ("no", "ad_hoc", "defined"),
        "A request that waits on one absent person either blocks work or gets approved "
        "outside the process.",
        "Define an ordered list of roles for each risk tier, with a timeout after which "
        "the request moves to the next role.",
    ),
    _question(
        "escalation.expiry",
        "Do approvals expire, and are they tied to the one request they approve?",
        ("no", "some", "all"),
        "An approval that never expires, or that covers more than one request, can be "
        "reused for something nobody approved.",
        "Give every approval an expiry and bind it to one request, so a retry with "
        "different parameters needs a new approval.",
    ),
    _question(
        "escalation.waiting",
        "While a request waits for a decision, what does the agent do?",
        ("proceeds", "held_no_rule", "rule_by_risk"),
        "If the agent proceeds while waiting, the approval is a formality. If there is no "
        "rule, behaviour depends on how each agent was built.",
        "Set a rule by risk tier: high-risk actions wait for the decision, and low-risk "
        "reads may continue only where that is decided in advance.",
    ),
    _question(
        "policy_change.second_person",
        "Does a change to an agent's permissions need approval from someone other than its author?",
        ("no", "sometimes", "always"),
        "One person who can both write and approve a permission change can widen an "
        "agent's reach on their own.",
        "Require a second person to approve every permission change. Where one person "
        "holds both roles, add a waiting period between writing and approving.",
    ),
    _question(
        "policy_change.owners",
        "Does a change that affects a team's systems need that team's sign-off?",
        ("no", "informal", "required"),
        "Teams whose systems an agent can reach find out about a change only after "
        "something goes wrong.",
        "Record which team owns each tool or system, and require sign-off from every team "
        "a change touches.",
    ),
    _question(
        "policy_change.versioning",
        "Can you say which version of the rules was in force when a given decision was made?",
        ("no", "partly", "yes"),
        "Without versions, a past decision cannot be checked against the rules that "
        "applied at the time.",
        "Version every change to the rules and record the version in force with each decision.",
    ),
    _question(
        "record.decisions",
        "Is every approval and refusal recorded with who decided, when and why?",
        ("no", "partly", "yes"),
        "Decisions kept in chat or email cannot be found, counted or shown to an auditor.",
        "Record each approval and refusal in one place, with the decider, the time, the "
        "request and a short reason for high-risk decisions.",
    ),
    _question(
        "record.tamper",
        "Could the record be changed or deleted without anyone noticing?",
        ("yes", "logged", "no"),
        "A record that can be edited quietly does not show what happened, only what it says now.",
        "Keep the record append-only, or chain each entry to the one before it so that "
        "any change can be detected.",
    ),
    _question(
        "record.identity",
        "Does the record show both the agent and the person it acted for, with a distinct "
        "identity for each agent?",
        ("no", "agent_only", "both"),
        "If agents share keys, or the record shows only the agent, nobody can say which "
        "agent acted or for whom.",
        "Give each agent its own identity, stop sharing keys between agents, and record "
        "the person an agent acts for alongside the agent.",
    ),
)

QUESTIONS_BY_ID = {question.id: question for question in QUESTIONS}
