"""The gap review's areas, questions and option levels (docs/SPEC.md section 8.1)."""

from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    GAP = "gap"
    NEEDS_VALIDATION = "needs_validation"
    PARTIAL = "partial"
    IN_PLACE = "in_place"
    NOT_ANSWERED = "not_answered"


LEVEL_LABELS = {
    Level.GAP: "Gap",
    Level.NEEDS_VALIDATION: "Needs validation",
    Level.PARTIAL: "Partial",
    Level.IN_PLACE: "In place",
    Level.NOT_ANSWERED: "Not answered",
}

# Lower ranks are weaker; an area takes the weakest answered question.
LEVEL_RANK = {Level.GAP: 0, Level.NEEDS_VALIDATION: 1, Level.PARTIAL: 2, Level.IN_PLACE: 3}

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
            # Not known is not a known gap (docs/SPEC.md section 8.1).
            UNKNOWN: Level.NEEDS_VALIDATION,
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
        "For each agent, is it written down what it can access and do, and which of its "
        "actions are high risk?",
        ("none", "some", "all"),
        "If what an agent can access and do is not written down, nobody can tell whether a "
        "request is inside its normal permissions or needs a decision.",
        "For each agent, list the tools and data it can access and the actions it can take, "
        "and mark each action low, medium or high risk.",
    ),
    _question(
        "agents.new_tools",
        "When a new tool or capability becomes available to an agent, what happens before "
        "the agent can use it?",
        ("automatic", "informal", "needs_decision"),
        "A tool added to a server an agent already uses can become usable at once, without "
        "anyone deciding that the agent should have it.",
        "Treat every new tool or capability as not permitted until a named role decides "
        "whether the agent may use it and at which risk tier.",
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
        "Can an agent's owner, or the person who asked the agent to do the task, approve that "
        "agent's own request?",
        ("yes", "not_prevented", "prevented"),
        "When the requester or the agent's owner can approve, there is no second person "
        "and the approval adds no control.",
        "Prevent the agent's owner and the person who asked the agent to do the task from "
        "approving its requests above the low risk tier, and enforce it in the approval tool.",
    ),
    _question(
        "approval.context",
        "When someone approves, do they see exactly what they are approving: the agent, "
        "the person who asked the agent to do the task, the tool and the parameters?",
        ("no", "partly", "yes"),
        "An approver who sees only a summary can approve something different from what runs.",
        "Show the approver the agent, the person who asked the agent to do the task, the "
        "tool and the exact parameters, and tie the approval to that one request.",
    ),
    _question(
        "escalation.path",
        "If the approver does not respond, does the request move to someone else?",
        ("no", "ad_hoc", "defined"),
        "A request that waits on one absent person either blocks work or gets approved "
        "outside the process.",
        "Define an ordered list of roles for each risk tier, with a timeout after which "
        "the request moves to the next role. If nobody answers at the last step, the "
        "request is refused, not approved.",
    ),
    _question(
        "escalation.expiry",
        "Do approvals expire, and are they tied to the one request they approve?",
        ("no", "some", "all"),
        "An approval that never expires, or that covers more than one request, can be "
        "reused for something nobody approved.",
        "Give every approval an expiry and bind it to one request, so a retry with "
        "different parameters needs a new approval. The action that runs must match the "
        "approved request.",
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
        "escalation.stop",
        "Can an agent be suspended immediately during a run, and is it decided who may do it?",
        ("no", "informal", "named_role"),
        "If an agent cannot be stopped mid-run, a wrong action continues until it finishes. "
        "If nobody is named to stop it, nobody acts in time.",
        "Provide a way to suspend an agent immediately during a run, and name the roles "
        "that may do it, including at least one outside the team that built the agent.",
    ),
    _question(
        "policy_change.second_person",
        "Does a change to an agent's permissions need approval from someone other than its author?",
        ("no", "sometimes", "always"),
        "One person who can both write and approve a permission change can widen what an "
        "agent can access and do on their own.",
        "Require a second person to approve every permission change. If only one person "
        "exists, record it as an accepted risk and notify someone outside the team of each "
        "change. A delay only makes the change visible; it is not a substitute.",
    ),
    _question(
        "policy_change.owners",
        "Does a change that affects a team's systems need that team's sign-off?",
        ("no", "informal", "required"),
        "Teams whose systems an agent can access find out about a change only after "
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
        "Does the record show both the agent and the person who asked the agent to do the "
        "task, with a distinct identity for each agent?",
        ("no", "agent_only", "both"),
        "If agents share keys, or the record shows only the agent, nobody can say which "
        "agent acted or who asked it to.",
        "Give each agent its own identity, stop sharing keys between agents, and record "
        "the person who asked the agent to do the task alongside the agent.",
    ),
)

QUESTIONS_BY_ID = {question.id: question for question in QUESTIONS}
