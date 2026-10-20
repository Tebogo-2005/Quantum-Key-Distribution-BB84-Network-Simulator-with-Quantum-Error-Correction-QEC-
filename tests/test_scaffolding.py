"""Guards the Phase-1 scaffold: package layout, import surface and dependency pins.

These tests are deliberately dependency-light (pytest + stdlib) so they pass
before the quantum stack is installed and fail loudly if the scaffold drifts.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("network", "quantum_core", "utils")
PINNED_PACKAGES = ("pennylane", "numpy", "matplotlib", "pytest")
PIN_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)==(?P<version>\d+\.\d+\.\d+)$")


@pytest.mark.parametrize("package", PACKAGES)
def test_layer_imports_and_documents_its_contract(package: str) -> None:
    module = importlib.import_module(f"src.{package}")

    assert module.__doc__, f"src/{package}/__init__.py must describe the layer"
    assert "Planned modules" in module.__doc__, (
        f"src/{package}/__init__.py must enumerate its planned modules"
    )


def test_package_version_is_semver() -> None:
    import src

    assert re.fullmatch(r"\d+\.\d+\.\d+", src.__version__), "src.__version__ must be X.Y.Z"


def test_architecture_document_covers_the_required_topics() -> None:
    document = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    for topic in ("Alice", "Bob", "Eve", "QBER", "syndrome", "ClassicalChannel"):
        assert topic in document, f"ARCHITECTURE.md is missing the {topic!r} contract"


def test_requirements_pin_every_direct_dependency_exactly() -> None:
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {}
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        match = PIN_PATTERN.match(entry)
        assert match, f"unpinned or non-exact requirement: {entry!r}"
        pins[match.group("name").lower()] = match.group("version")

    missing = set(PINNED_PACKAGES) - set(pins)
    assert not missing, f"requirements.txt is missing exact pins for: {sorted(missing)}"

    major, minor = map(int, pins["numpy"].split(".")[:2])
    assert (major, minor) >= (2, 0), "PennyLane 0.45 requires NumPy >= 2.0"


def test_no_placeholder_instead_of_architecture() -> None:
    document = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert len(document.splitlines()) >= 120, "ARCHITECTURE.md must contain the full design"
    for placeholder in ("TODO", "TBD", "Lorem ipsum"):
        assert placeholder not in document, f"ARCHITECTURE.md still contains {placeholder!r}"
