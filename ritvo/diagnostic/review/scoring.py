"""Load a gap review answers file and score it (docs/SPEC.md section 8.1)."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from ritvo.diagnostic.review.questions import (
    AREAS,
    LEVEL_RANK,
    QUESTIONS,
    QUESTIONS_BY_ID,
    Area,
    Level,
    Question,
)

REVIEW_VERSION = 0


class ReviewError(Exception):
    """The answers file cannot be read or is not valid. Nothing is written."""


class _AnswersFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    review_version: StrictInt
    organisation: str | None = None
    # YAML reads a bare yes or no as a boolean; both are option IDs, so they are mapped back.
    answers: dict[str, str | bool | None] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class Answers:
    organisation: str | None
    answers: dict[str, str]
    notes: dict[str, str]
    sha256: str


@dataclass(frozen=True)
class QuestionResult:
    question: Question
    answer: str | None
    level: Level
    note: str | None


@dataclass(frozen=True)
class AreaResult:
    area: Area
    level: Level
    questions: tuple[QuestionResult, ...]

    @property
    def answered(self) -> int:
        return sum(1 for result in self.questions if result.answer is not None)

    @property
    def incomplete(self) -> bool:
        return 0 < self.answered < len(self.questions)


@dataclass(frozen=True)
class Review:
    organisation: str | None
    answers_sha256: str
    areas: tuple[AreaResult, ...]

    @property
    def answered(self) -> int:
        return sum(area.answered for area in self.areas)

    @property
    def total(self) -> int:
        return len(QUESTIONS)


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _answer_text(value: str | bool | None) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


def load_answers(path: Path) -> Answers:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReviewError("the answers file is not UTF-8 text") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise ReviewError(f"the answers file is not valid YAML{where}") from None
    if not isinstance(data, dict):
        raise ReviewError("the answers file must be a YAML mapping")
    try:
        parsed = _AnswersFile.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        ]
        raise ReviewError("; ".join(problems)) from None

    problems = []
    if parsed.review_version != REVIEW_VERSION:
        problems.append(f"review_version must be {REVIEW_VERSION}")
    valid_ids = ", ".join(QUESTIONS_BY_ID)
    answers: dict[str, str] = {}
    for qid, value in parsed.answers.items():
        question = QUESTIONS_BY_ID.get(qid)
        if question is None:
            problems.append(f"unknown question ID {_short(qid)} in answers; valid IDs: {valid_ids}")
            continue
        answer = _answer_text(value)
        if answer is None:
            continue
        if answer not in question.options:
            valid_options = ", ".join(question.options)
            problems.append(
                f"{qid}: unknown option {_short(answer)}; valid options: {valid_options}"
            )
            continue
        answers[qid] = answer
    for qid in parsed.notes:
        if qid not in QUESTIONS_BY_ID:
            problems.append(f"unknown question ID {_short(qid)} in notes; valid IDs: {valid_ids}")
    if problems:
        raise ReviewError("; ".join(problems))
    return Answers(
        organisation=parsed.organisation,
        answers=answers,
        notes=dict(parsed.notes),
        sha256=digest,
    )


def _area_level(results: tuple[QuestionResult, ...]) -> Level:
    answered = [result.level for result in results if result.answer is not None]
    if not answered:
        return Level.NOT_ANSWERED
    level = min(answered, key=LEVEL_RANK.__getitem__)
    if len(answered) < len(results) and level is Level.IN_PLACE:
        return Level.PARTIAL
    return level


def score(answers: Answers) -> Review:
    areas = []
    for area in AREAS:
        results = tuple(
            QuestionResult(
                question=question,
                answer=answers.answers.get(question.id),
                level=(
                    question.options[answers.answers[question.id]]
                    if question.id in answers.answers
                    else Level.NOT_ANSWERED
                ),
                note=answers.notes.get(question.id),
            )
            for question in QUESTIONS
            if question.area == area
        )
        areas.append(AreaResult(area=area, level=_area_level(results), questions=results))
    return Review(
        organisation=answers.organisation,
        answers_sha256=answers.sha256,
        areas=tuple(areas),
    )
