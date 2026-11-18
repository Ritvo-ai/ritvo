"""Ask the questions an answers file leaves open (docs/SPEC.md section 8.6)."""

from collections.abc import Callable
from pathlib import Path

from ritvo.diagnostic.review.answers_file import render_answers
from ritvo.diagnostic.review.questions import QUESTIONS, UNKNOWN, Question
from ritvo.diagnostic.review.scoring import OLD_REVIEW_VERSION, Basis, ReviewError, load_answers


def interview(path: Path, ask: Callable[[Question], str]) -> tuple[str, int] | None:
    """Return the new file's text and how many answers were recorded, or None if nothing is open.

    `ask` returns an option ID, `unknown`, or an empty string to leave the question open. Every
    answer is recorded as asserted with no evidence: a person said so.
    """
    current = load_answers(path, allow_draft=True)
    if current.version == OLD_REVIEW_VERSION:
        raise ReviewError(
            f"{path.name} is review_version {OLD_REVIEW_VERSION}; "
            "run ritvo review on it and write review_version 1 first"
        )
    open_questions = [question for question in QUESTIONS if question.id not in current.answers]
    if not open_questions:
        return None
    answers = dict(current.answers)
    basis = dict(current.basis)
    for question in open_questions:
        reply = ask(question)
        if reply:
            answers[question.id] = reply
            basis[question.id] = Basis(source="asserted", evidence=None)
    recorded = len(answers) - len(current.answers)
    text = render_answers(current.organisation, answers, basis, dict(current.notes), current.draft)
    return text, recorded


def valid_reply(question: Question, reply: str) -> str | None:
    """None when the reply is acceptable, otherwise the problem."""
    if reply == "" or reply == UNKNOWN or reply in question.options:
        return None
    choices = ", ".join([*question.options, UNKNOWN])
    return f"answer with one of: {choices}, or press enter to leave it open"
