"""Render a scored gap review as one self-contained HTML page (docs/SPEC.md section 8.1)."""

from datetime import UTC, date, datetime
from pathlib import Path

import jinja2

from ritvo import __version__
from ritvo.diagnostic.review.questions import LEVEL_LABELS, Level
from ritvo.diagnostic.review.scoring import Review

_ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("ritvo.diagnostic.review", "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_html(review: Review, generated: date | None = None) -> str:
    template = _ENV.get_template("review.html.j2")
    return template.render(
        review=review,
        generated=(generated or datetime.now(UTC).date()).isoformat(),
        version=__version__,
        labels={level.value: label for level, label in LEVEL_LABELS.items()},
        gap_areas=sum(1 for area in review.areas if area.level is Level.GAP),
        Level=Level,
    )


def write_report(review: Review, out: Path, generated: date | None = None) -> None:
    out.write_text(render_html(review, generated), encoding="utf-8", newline="\n")
