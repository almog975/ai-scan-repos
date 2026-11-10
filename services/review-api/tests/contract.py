"""Validate payloads against contracts/ (the source of truth)."""
import json
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@lru_cache
def _registry():
    reg = Registry()
    for p in CONTRACTS.glob("*.schema.json"):
        r = Resource.from_contents(json.loads(p.read_text()))
        reg = reg.with_resource(p.name, r).with_resource(r.contents["$id"], r)
    spec = yaml.safe_load((CONTRACTS / "review-api.openapi.yaml").read_text())
    spec.setdefault("$schema", "https://json-schema.org/draft/2020-12/schema")
    reg = reg.with_resource("urn:review-api", Resource.from_contents(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:review-api", **spec}))
    return reg, spec


def spec():
    return _registry()[1]


def validator(name: str):
    reg, _ = _registry()
    if isinstance(name, dict):
        schema = name
    elif name.endswith(".json"):
        schema = {"$ref": name}
    else:
        schema = {"$ref": f"urn:review-api#/components/schemas/{name}"}
    return Draft202012Validator(schema, registry=reg, format_checker=FormatChecker())


def check(name: str, payload):
    errs = sorted(validator(name).iter_errors(payload), key=str)
    assert not errs, f"{name}: " + "; ".join(e.message for e in errs[:5])


def common_enum(name: str) -> list:
    return json.loads((CONTRACTS / "common.schema.json").read_text())["$defs"][name]["enum"]
