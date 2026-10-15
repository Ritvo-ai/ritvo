"""Write the blank intake templates (docs/SPEC.md section 8.2)."""

import csv
from pathlib import Path

from ritvo.diagnostic.intake.columns import INTAKE_FILES
from ritvo.diagnostic.intake.reader import IntakeError


def write_templates(directory: Path) -> tuple[Path, ...]:
    """Write each file as a header row only. Writes nothing if any of them exists."""
    if directory.exists() and not directory.is_dir():
        raise IntakeError(f"{directory} is not a directory")
    targets = tuple(directory / file.name for file in INTAKE_FILES)
    existing = [target.name for target in targets if target.exists()]
    if existing:
        raise IntakeError(f"already in {directory}: {', '.join(existing)}; nothing was written")
    directory.mkdir(parents=True, exist_ok=True)
    for file, target in zip(INTAKE_FILES, targets, strict=True):
        with target.open("x", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerow(file.column_names)
    return targets
