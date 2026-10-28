"""Shared helpers for Phase 2 tests."""
import json
from pathlib import Path

P2 = Path(__file__).parent / "fixtures" / "phase2"


def p2(rel):
    return json.loads((P2 / rel).read_text())


def all_text(root: Path) -> str:
    """Concatenated content of every file under root (to grep for leaked secrets)."""
    return "\n".join(p.read_text(errors="replace") for p in Path(root).rglob("*") if p.is_file())
