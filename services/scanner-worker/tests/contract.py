"""Validate worker output against contracts/*.schema.json (source of truth)."""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@lru_cache
def _registry():
    reg = Registry()
    for p in CONTRACTS.glob("*.schema.json"):
        r = Resource.from_contents(json.loads(p.read_text()))
        reg = reg.with_resource(p.name, r).with_resource(r.contents["$id"], r)
    return reg


def check(name: str, payload):
    v = Draft202012Validator({"$ref": name}, registry=_registry(), format_checker=FormatChecker())
    errs = sorted(v.iter_errors(payload), key=str)
    assert not errs, f"{name}: " + "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errs[:5])
