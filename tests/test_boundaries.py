"""The boundary rule: the Governance OS and the enforcement side never import each other."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "ritvo"
SIDES = {
    "governance_os": {"enforcement", "mock_control_plane", "adapters"},
    "enforcement": {"governance_os"},
    "mock_control_plane": {"governance_os"},
    "adapters": {"governance_os"},
    "inspector": {"governance_os"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("side", SIDES)
def test_no_side_imports_one_it_must_not(side: str) -> None:
    folder = PACKAGE / side
    if not folder.is_dir():
        pytest.skip(f"{side} does not exist yet")
    for path in folder.rglob("*.py"):
        for module in _imports(path):
            parts = module.split(".")
            if parts[0] == "ritvo" and len(parts) > 1:
                assert parts[1] not in SIDES[side], f"{path.relative_to(PACKAGE)} imports {module}"


def test_the_contract_and_shared_packages_import_neither_side() -> None:
    forbidden = {"governance_os", "enforcement", "mock_control_plane", "adapters", "inspector"}
    for name in ("adapter_contract", "ledger", "secrets", "config"):
        for path in (PACKAGE / name).rglob("*.py"):
            for module in _imports(path):
                parts = module.split(".")
                if parts[0] == "ritvo" and len(parts) > 1:
                    assert parts[1] not in forbidden, (
                        f"{path.relative_to(PACKAGE)} imports {module}"
                    )
