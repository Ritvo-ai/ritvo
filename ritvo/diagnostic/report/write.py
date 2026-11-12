"""Write the four files of a diagnosis (docs/SPEC.md section 8.5)."""

import hashlib
import json
from pathlib import Path

from ritvo.diagnostic.gaps.model import Diagnosis
from ritvo.diagnostic.report.draft import draft_text
from ritvo.diagnostic.report.gap_report import render_html
from ritvo.diagnostic.report.matrix import matrix_text

DIAGNOSIS_DIR = "diagnosis"
DIAGNOSIS = "diagnosis.json"
DRAFT = "ritvo.draft.yaml"
MATRIX = "authority-matrix.csv"
REPORT = "gap-report.html"


def diagnosis_text(diagnosis: Diagnosis) -> str:
    return json.dumps(diagnosis.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def write_diagnosis(diagnosis: Diagnosis, out: Path) -> list[Path]:
    """Write the files into `out`, creating it, and replace any earlier copy of the four."""
    text = diagnosis_text(diagnosis)
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Everything is built before anything is written, so a failure leaves the folder as it was.
    contents = [
        (DIAGNOSIS, text.encode("utf-8")),
        (DRAFT, draft_text(diagnosis).encode("utf-8")),
        (MATRIX, matrix_text(diagnosis).encode("utf-8")),
        (REPORT, render_html(diagnosis, sha256).encode("utf-8")),
    ]
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in contents:
        (out / name).write_bytes(data)
        paths.append(out / name)
    return paths
