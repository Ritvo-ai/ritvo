"""Write an answers file as YAML (docs/SPEC.md sections 8.1, 8.3 and 8.6)."""

import yaml

from ritvo.diagnostic.review.questions import QUESTIONS
from ritvo.diagnostic.review.scoring import Basis


def render_answers(
    organisation: str | None,
    answers: dict[str, str],
    basis: dict[str, Basis],
    notes: dict[str, str],
    draft: bool,
) -> str:
    """Questions are written in the order of section 8.1, so one input gives one file."""
    order = [question.id for question in QUESTIONS if question.id in answers]
    data: dict[str, object] = {"review_version": 1}
    if draft:
        data["draft"] = True
    if organisation is not None:
        data["organisation"] = organisation
    data["answers"] = {qid: answers[qid] for qid in order}
    data["basis"] = {
        qid: (
            {"source": basis[qid].source}
            if basis[qid].evidence is None
            else {"source": basis[qid].source, "evidence": basis[qid].evidence}
        )
        for qid in order
        if qid in basis
    }
    data["notes"] = {qid: notes[qid] for qid in order if qid in notes}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
